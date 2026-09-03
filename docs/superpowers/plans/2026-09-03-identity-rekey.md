# Identity Re-key + Twin Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the render pipeline splitting one library item into two `media_items` rows when Plex renumbers it, and reconcile the pairs that already exist — a runtime re-key in `process_item` plus a dry-run-first twin-merge maintenance job.

**Architecture:** `media_items` is keyed on the Plex rating key, but the rating key is a *hint* the resolver is free to refuse: `PlexClient.resolve` returns the key it *found*, and `_upsert_media_item`'s `INSERT … ON CONFLICT (rating_key)` therefore **inserts a brand-new row** whenever the resolved key differs from the stored one. The original row keeps its render rows, its facts, its dismissals, its `logo_upload_key` and its children, and is never written again. **T1** closes that at the fork: after `resolve()` and before the first upsert, `process_item` proves from the database that the resolved key is *free* and that *exactly one* row carries the resolved *identity* (same kind, same library, same season/episode coordinates, and a non-empty intersection of external ids), takes that row `FOR UPDATE`, and moves it onto the live key — with an `IntegrityError` fall-through so a concurrent winner degrades to the ordinary upsert instead of crashing. Because the precondition is an *identity* test rather than a key comparison, it also closes the two producers that carry no key at all (the ratings-drift sweep and the webhook intake); T1 additionally makes the drift sweep carry the row's key and makes `arr_sync` discovery enqueue the *stale row's* intent when an identity match exists under a different key, so discovery repairs instead of duplicating. **T2** reconciles the backlog as a scheduled job in the `make_prune_job` mould — `plex_merge`, dry-run by default, capped, refusable, resumable — electing the survivor by newest key in the dry run and verifying it live before any delete, repointing renders/dismissals/facts/credits per art_kind, **repointing children before the stale row is deleted** (the self-FK cascades), reusing `retire`'s `(id, updated_at)` guarded delete and `dismiss_jobs_for` on *both* keys. **Nothing moves on disk:** `naming.asset_path` is keyed by library + root folder, never by the rating key.

**Tech Stack:** Python 3.14 / SQLAlchemy 2.0 async / PostgreSQL 18 / FastAPI / pytest + pytest-asyncio in the `test` compose service. No Alembic migration — the only schema-adjacent change is two new config sections, which are Pydantic models, not tables.

---

## Global Constraints

Every task's requirements implicitly include this section. Read it before the first step of any task.

1. **Branch.** `feat/identity-rekey` cuts from **`origin/main` AFTER PR #142 (`feat/overlay-ratings`, sub-phase C2a) has merged.** #142 touches `src/autoposter/render/pipeline.py`, which T1 also edits; cutting before it merges guarantees a conflict in the one file this phase cannot afford one in. **Task 1 Step 1 is the content probe that proves #142 landed. If the probe fails, STOP and report — do not cut the branch, do not proceed.** Re-verify the tip at execution time; do not trust any sha written in this document (`d7d437a` was `origin/main` when it was written).
2. **No migration.** This phase adds no table and no column. `tests/test_migrations.py` must stay green with **exactly one alembic head**, unchanged. If a step appears to need a migration, that is a new adjudication — STOP and report it.
3. **The identity predicate, verbatim.** A row carries the resolved item's identity when **all** of these hold:
   - `media_items.kind == item.kind`, and
   - `media_items.library == item.library`, and
   - `media_items.season_number IS NOT DISTINCT FROM item.season_number`, and
   - `media_items.episode_number IS NOT DISTINCT FROM item.episode_number`, and
   - at least one of `tmdb_id` / `tvdb_id` / `imdb_id` is non-null on the resolved item **and equal** on the row (a non-empty intersection).

   The season/episode coordinates are part of "same kind at the same level" — without them every season of one show matches every other and the exactly-one guard refuses them all. A row with no external id is **never** re-keyed on a title match.
4. **Cross-library, id-disjoint and kind-mismatch are NOT re-keys.** They are logged and left to the merge job. In particular the **A2 population** — adopted season/episode rows, which stored each episode's *own* external ids while `resolve()` reports the *show's* ids — is id-disjoint by construction and must therefore be *missed* by T1 and picked up by T2. This is deliberate. Do not widen the predicate to reach it.
5. **The re-key's precondition is proved from the database, never from a key comparison.** Two facts, in this order: (a) no row already holds the resolved key; (b) exactly one row carries the resolved identity. `(a)` failing means the twin already exists → T2's work, not T1's. `(b)` yielding 0 means an ordinary new item → insert as today. `(b)` yielding more than 1 means ambiguity → WARNING, no re-key.
6. **`FOR UPDATE` and an `IntegrityError` fall-through.** The candidate row is selected `FOR UPDATE`; the write is an ORM mutation flushed inside a `try`; `IntegrityError` (a concurrent worker won the key) is caught, `rollback()`ed, logged, and degrades to the ordinary upsert. **No job may fail because of a lost re-key race.**
7. **Nothing moves on disk, ever.** `render/naming.py`'s `asset_path(config, library, root_folder, art_kind, season_number, episode_number)` takes no rating key. A re-key rewrites no `asset_path`; a merge repoints render rows whose `asset_path` strings are byte-identical. `asset_cleanup` is correct as shipped and is not touched. **T1 carries a test that asserts `renders.asset_path` is unchanged across a re-key.**
8. **The cascade law.** `media_items.parent_id` is a self-FK with `ondelete="CASCADE"` (`src/autoposter/db/models.py:35-37`), and `renders` (`:79-81`), `item_facts` (`:294-296`), `item_credits` (`:366-369`) and `action_dismissals` (`:700-702`) all cascade from `media_items.id`. Therefore in T2 **`UPDATE media_items SET parent_id = <survivor> WHERE parent_id = <stale>` runs BEFORE the stale row is deleted**, in the same transaction, or the delete takes every live child with it. A test seeds a show pair with stale seasons and asserts no cascade loss.
9. **The merge is a JOB, not a migration.** Dry run by default (`merge.apply: false`), plausibility-capped, refusable, resumable (one committed transaction per pair), with a dashboard Run-now — the `plex_prune` mould exactly. `"plex_merge"` **must** be added to `SCHEDULED_JOB_NAMES` (`src/autoposter/api/routes.py:93-102`) in the same commit that creates the job, and registered in `src/autoposter/app.py` beside `make_prune_job` — the reclaim-hotfix precedent: a job absent from that frozenset has no Run-now and answers 404.
10. **Survivor election: newest key in the dry run, live-verified at apply.** The dry run is **probe-free** so an operator can read the report with Plex down; it elects the row whose rating key is the larger integer. The applied pass probes **both** rows by key and merges only the pair where the survivor's key resolves and the stale one's does not — refusing (and counting separately) the both-resolve pair (two real Plex items: an operator decision), the neither-resolve pair (`plex_prune`'s population) and the pair where the election and the probe disagree.
11. **Per-art_kind render repointing.** For each art kind the stale row has: the survivor **lacks** it → repoint (`UPDATE renders SET item_id = survivor`); the survivor **has** it → drop the stale row's (the survivor's is the one being scored). Both counts are reported **separately** — deleted duplicates shrink the Action Center's denominator, repointed orphans stay in it and become finishable, and an operator must not read the first as data loss.
12. **Dismissals are repointed, and they honestly re-surface.** `action_dismissals.evidence` hashes render fact columns including whether the row is scored (`src/autoposter/actions/flags.py`'s `_EVIDENCE_COLUMNS`), so a dismissal made against the stale row's unscored facts will not match the survivor's scored ones and the row returns to the queue. That is the dismissal contract (`db/models.py:670-692`), not a bug. It is said in the job's docstring, in the summary string, and in `deploy/README.md`.
13. **`dismiss_jobs_for` on BOTH keys.** In-flight and parked `process_item` payloads carry the dead key. `scheduler/prune.py`'s `dismiss_jobs_for` is **imported and reused verbatim** — not reimplemented — and is called with the stale key *and* the survivor key for every applied merge.
14. **`retire`'s guarded delete is reused in shape.** The stale row is deleted `WHERE id = :id AND updated_at = :updated_at`, exactly like `scheduler/prune.py:399-407`, so a row a worker re-upserted under the pass survives and is counted skipped.
15. **DB-clock-safe.** Order every scan and every walk by `MediaItem.id`. Never assert wall-clock ordering, never sleep to make two timestamps differ, never compare a duration. (The dev machine's Docker clock steps backwards ~2.7s every ~27s.)
16. **T1 measures the suite baseline; it never inherits one.** Run the full backend suite at the start of T1, record the total, and report every later delta against **that measured number**.
17. **Refuse loudly.** Every refusal is a returned summary string (a pass that ran and declined) or a `MergeRefused` raise (a pass that broke off) — never a silent skip and never a partial write with no count. A refusal message names an exception **class** only: never `str(exc)`, never a URL, never a token. `MergeRefused.served_detail = True`, like `PruneRefused`.
18. **Files that stay untouched.** No edit of any kind to `src/autoposter/render/naming.py`, `src/autoposter/render/compositor.py`, anything under `src/autoposter/badges/`, or anything under `src/autoposter/overlays/`. The five parity pin files are never edited: `tests/test_badge_spec.py`, `tests/test_badge_geometry.py`, `tests/test_badge_draw.py`, `tests/test_badge_compose.py`, `tests/test_badge_parity.py`. The golden gate `tests/test_overlay_engine_golden.py` (`POSTER_PIXELS_SHA`, `TITLE_CARD_PIXELS_SHA`) is never edited and must keep passing unmodified.
19. **House code style.** Comments explain *why*, in prose, citing the concrete failure they prevent. Double-hyphen `--` for em dashes in Python comments. Lines wrap at 100 columns; `ruff check .` from the repo root must be clean.
20. **Container discipline.** The suite runs in the container, never on the host. One compose project per task — **`pirk1`, `pirk2`, `pirk3`** — always with the `.superpowers/isolated-db.yml` overlay. Use this exact shape:

    ```bash
    docker compose -p pirk<N> -f docker-compose.yml -f .superpowers/isolated-db.yml \
      run --name pirk<N>-run test sh -c \
      "set -o pipefail; pytest <paths> -q 2>&1 | tee /app/.superpowers/run-pirk<N>.log"
    ```

    **No `--rm`** — the container's output must survive so the log can be read back (`rtk proxy` filters streamed logs and `--rm` deletes the container; the tee'd file is the record). If the command times out, wait on it in the **foreground** with `docker wait pirk<N>-run`. Remove a finished run container with `docker rm pirk<N>-run` before reusing the name. Tear down with `docker compose -p pirk<N> -f docker-compose.yml -f .superpowers/isolated-db.yml down` — **never** `down -v`.
21. **Commits and attribution.** Conventional commit messages, staged **by name** (never `git add -A`). **No `Co-Authored-By` trailer, no mention of AI, Claude or any assistant** in any commit message, PR body, code comment or document. Add `--no-gpg-sign` **only** if signing blocks or times out.
22. **`.superpowers/` is gitignored.** Nothing committed by this phase may cite a path under `.superpowers/` as its record. The durable record is `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` and `deploy/README.md`.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `src/autoposter/render/pipeline.py` | `REKEY_SOURCE`/`REKEY_EVENT` constants, `_identity_clauses`, `_identity_candidates`, `_rekey_by_identity`; one call in `process_item` after the fork WARNING and before the first `_upsert_media_item` | T1 |
| `src/autoposter/scheduler/jobs.py` | `_stamp_and_enqueue`'s `RenderIntent` carries `rating_key=item.rating_key` | T1 |
| `src/autoposter/arr/sync.py` | `_stale_rows_by_key` helper; `enqueue_unknown_items` builds the intent from the stale row when an identity match exists under a different key | T1 |
| `tests/test_pipeline_rekey.py` | **NEW.** Every T1 pin, driven through the real `process_item` | T1 |
| `tests/test_scheduler_drift_job.py` | one pin: the drift intent carries the row's rating key | T1 |
| `tests/test_arr_safety_net.py` | two pins: discovery reuses the stale row; an ambiguous identity falls back to today's behaviour | T1 |
| `src/autoposter/plex/client.py` | `_key_resolves_sync` / `keys_resolve` — the public key-level acceptance probe the survivor election needs | T2 |
| `src/autoposter/scheduler/merge.py` | **NEW.** The whole job: `find_mergeable`, `plan_merges`, `verify_survivors`, `merge`, `implausible_merge_count`, `make_merge_job` | T2 |
| `src/autoposter/config/schema.py` | `MergeConfig`; `Config.merge`; `SchedulerConfig.merge_days` | T2 |
| `config/autoposter.example.yaml` | the `merge:` section and `scheduler.merge_days` | T2 |
| `src/autoposter/api/routes.py` | `"plex_merge"` in `SCHEDULED_JOB_NAMES` | T2 |
| `src/autoposter/app.py` | `make_merge_job` registered beside `make_prune_job` | T2 |
| `tests/test_scheduler_merge_job.py` | **NEW.** Every T2 pin | T2 |
| `tests/test_plex.py` | `keys_resolve` pins | T2 |
| `tests/test_app.py` | one pin: `plex_merge` is in `scheduler_intervals` after boot | T2 |
| `tests/test_api_scheduled_runs.py` | one pin: `POST /api/scheduled-runs/plex_merge/run` is accepted | T2 |
| `src/autoposter/api/action_center.py` | `_backfill_state`'s docstring names the merge job, not the pruner, as the twins' owner | T3 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | the new row, files-and-closes; row 129's delivered note corrected | T3 |
| `deploy/README.md` | the `plex_merge` section, the run order, the adoption caveat, and the two sizing queries banked as the dry run's cross-check | T3 |

---

## Task Summary

| # | Task | Size |
|---|---|---|
| 1 | The identity re-key in `process_item`, and the two silent doors | M |
| 2 | The `plex_merge` maintenance job | M |
| 3 | Wrap: the corrected comment, the roadmap row, the operator documentation | S |

---

## Task 1: The identity re-key in `process_item`, and the two silent doors

**Files:**
- Modify: `src/autoposter/render/pipeline.py` (imports; new constants and three functions above `_upsert_media_item` at `:595`; one call in `process_item` after the fork WARNING at `:1486-1491` and before the first `_upsert_media_item` at `:1503`)
- Modify: `src/autoposter/scheduler/jobs.py:240-249` (`_stamp_and_enqueue`'s `RenderIntent`)
- Modify: `src/autoposter/arr/sync.py:321-378` (`enqueue_unknown_items`) and its import line
- Create: `tests/test_pipeline_rekey.py`
- Modify: `tests/test_scheduler_drift_job.py` (one new test)
- Modify: `tests/test_arr_safety_net.py` (two new tests)

**Interfaces:**
- Consumes: `pipeline._upsert_media_item(session, item) -> MediaItem`, `pipeline._get_or_create_render(session, media_item, art_kind, asset_path) -> Render`, `plex.resolve(intent) -> ResolvedItem`, `db.models.MediaItem` / `EventLog`, `intake.arr.RenderIntent`.
- Produces, for T2 and T3:
  - `render.pipeline.REKEY_SOURCE = "rekey"` and `render.pipeline.REKEY_EVENT = "media_item_rekeyed"` — the `events_log` identity of a re-key.
  - `async render.pipeline._rekey_by_identity(session: AsyncSession, item: ResolvedItem) -> str | None` — returns the **old** rating key when a row was re-keyed, `None` otherwise.
  - `async render.pipeline._identity_candidates(session: AsyncSession, item: ResolvedItem) -> list[MediaItem]` — the locked candidate rows; the seam the race test wraps.
  - `render.pipeline._identity_clauses(item: ResolvedItem) -> list` — the external-id half of the predicate as OR-able SQLAlchemy clauses.
  - `async arr.sync._stale_rows_by_key(session, kind: str, guids_by_key: dict[str, dict]) -> dict[str, MediaItem]`.

---

- [ ] **Step 1: Prove PR #142 has merged — the content probe. STOP if it has not.**

This phase must not cut its branch until sub-phase C2a is on `origin/main`; both edit `src/autoposter/render/pipeline.py`. Do not trust a PR page or a sha — probe for the modules C2a ships.

Run:

```bash
git fetch origin && \
  git show origin/main:src/autoposter/facts/mdblist.py | grep -c "def parse_ratings" && \
  git show origin/main:src/autoposter/badges/values.py | grep -c "def plex_native_ratings" && \
  git show origin/main:src/autoposter/overlays/families.py | grep -c "VERSIONS" && \
  git ls-tree origin/main assets/badges/images/versions.png
```

Expected: `1`, `1`, a count of `1` or more, and one `git ls-tree` line naming `assets/badges/images/versions.png`.

**If any of the four is `0` or empty, PR #142 has not merged. STOP. Report "C2a (#142) is not on origin/main; the identity re-key branch cannot cut yet" and do nothing else.**

- [ ] **Step 2: Cut the branch**

```bash
cd /d/Sites/autoposter && git checkout main && git pull --ff-only && \
  git checkout -b feat/identity-rekey && git log --oneline -1
```

Expected: a new branch at the current `origin/main` tip. **Record that sha** — the read-only check in Step 20 is measured from it.

- [ ] **Step 3: Measure the suite baseline (never inherit one)**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-baseline test sh -c \
  "set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pirk1-baseline.log"
```

If it times out, wait in the foreground with `docker wait pirk1-baseline`, then read `.superpowers/run-pirk1-baseline.log`.

Expected: a green run. **Write down the passed/skipped totals from that log.** Every later delta in this plan is reported against this measured number, not against any figure written in this document. Then `docker rm pirk1-baseline`.

- [ ] **Step 4: Write the failing pipeline tests**

Create `tests/test_pipeline_rekey.py`:

```python
"""The identity re-key, through the real ``process_item``.

``media_items`` is keyed on the Plex rating key, and the Plex rating key is a
*hint* the resolver is free to refuse: ``PlexClient.resolve`` returns the key
it FOUND, so a re-matched or renumbered item resolves to a key the stored row
does not hold, ``_upsert_media_item``'s ``ON CONFLICT (rating_key)`` conflicts
with nothing, and a SECOND row appears carrying every future render while the
original keeps its renders, its facts, its dismissals, its ``logo_upload_key``
and its children and is never written again.

Every test here drives ``process_item`` -- the pipeline's only entry point and
the only caller of ``render_artifact``. A helper-level test would prove the
predicate can be computed and say nothing about whether the shipped path uses
it, which is exactly how a gated feature has twice passed its own tests in
this tree while the wired path did something else.

Two properties are load-bearing and are pinned as hard as the re-key itself:
an item whose key has NOT moved must be byte-identical (same row, same key,
same render row, no audit row), and a re-key must move nothing on disk --
``naming.asset_path`` is keyed by library and root folder, never by the rating
key.
"""
import logging
from pathlib import Path

import httpx
from conftest import decodable_png
from sqlalchemy import select

from autoposter.config.loader import load_config
from autoposter.db.models import EventLog, MediaItem, Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.providers.base import ArtCandidate
from autoposter.render import pipeline
from autoposter.render.textfit import FitResult

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _config(tmp_path):
    """The example config, rooted in ``tmp_path``, with both Plex-touching
    stages off.

    ``operations`` and ``badges`` each want a live ``plexapi`` object from
    ``plex.fetch_item``, and neither says anything about which ``media_items``
    row the pipeline writes to -- which is the whole subject of this file.
    """
    config = load_config(EXAMPLE)
    config.assets_root = tmp_path / "assets"
    config.manual_assets_root = tmp_path / "manual"
    config.backup_root = tmp_path / "backup"
    config.fonts_root = tmp_path / "fonts"
    config.overlays_root = tmp_path / "overlays"
    config.operations.enabled = False
    config.badges.enabled = False
    return config


class _FakePlex:
    """Answers ``resolve`` with a fixed item, whatever it is asked.

    The resolver is not under test here: what is under test is what the
    pipeline does when the key ``resolve`` returns differs from the one the
    intent carried, so the answer is supplied rather than searched for. The
    same stand-in shape as ``tests/test_pipeline_facts.py``'s.
    """

    def __init__(self, item):
        self._item = item
        self.asked = []

    async def resolve(self, intent):
        self.asked.append(intent)
        return self._item

    async def fetch_item(self, rating_key):
        raise AssertionError(
            "operations and badges are off in this file; nothing may fetch a "
            "live Plex object"
        )


def _resolved(rating_key, **overrides):
    """What ``resolve()`` hands ``process_item`` for a movie."""
    fields = dict(
        rating_key=rating_key, library="Movies", kind="movie",
        title="Dune: Part Two", year=2024, season_number=None,
        episode_number=None, root_folder="Dune Part Two (2024)",
        file_path="/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id="tt15239678",
        parent_rating_key=None,
    )
    fields.update(overrides)
    return ResolvedItem(**fields)


async def _row(session, rating_key, **columns):
    """One committed ``media_items`` row.

    Committed so its ``updated_at`` is a settled value written by its own
    transaction -- the same reason ``tests/test_scheduler_prune_job.py``'s
    ``_add_item`` commits.
    """
    fields = dict(
        library="Movies", kind="movie", title="Dune: Part Two", year=2024,
        tmdb_id=693134, imdb_id="tt15239678",
    )
    fields.update(columns)
    item = MediaItem(rating_key=rating_key, **fields)
    session.add(item)
    await session.commit()
    return item


def _row_level_render_artifact(monkeypatch):
    """Replace ``render_artifact`` with its own row-level head, and record it.

    Not a bare stub. The claim under test is *which ``media_items`` row the
    render row lands on*, and a stub that merely recorded its ``item``
    argument would prove the argument rather than the row. So this calls the
    REAL ``_upsert_media_item`` and the REAL ``_get_or_create_render`` -- the
    two functions that actually decide -- and skips only the provider fetch
    and the ImageMagick work. ``test_the_real_render_scores_the_re_keyed_row``
    below drives the genuine ``render_artifact`` for the end-to-end claim.

    Returns the list it appends ``(media_item_id, art_kind)`` to.
    """
    seen: list[tuple[int, str]] = []

    async def fake(session, config, http, item, art_kind, providers):
        media_item = await pipeline._upsert_media_item(session, item)
        target = pipeline.naming.asset_path(
            config, item.library, item.root_folder, art_kind,
            item.season_number, item.episode_number,
        )
        render = await pipeline._get_or_create_render(
            session, media_item, art_kind, target
        )
        render.status = "rendered"
        await session.commit()
        seen.append((media_item.id, art_kind))
        return render

    monkeypatch.setattr(pipeline, "render_artifact", fake)
    return seen


async def _items(session):
    return (
        await session.execute(select(MediaItem).order_by(MediaItem.id))
    ).scalars().all()


async def _audits(session):
    return (
        await session.execute(
            select(EventLog).where(EventLog.source == pipeline.REKEY_SOURCE)
        )
    ).scalars().all()


# --- the re-key itself ----------------------------------------------------


async def test_a_re_matched_item_re_keys_its_own_row_and_creates_no_twin(
    session, tmp_path, monkeypatch
):
    """The whole defect, in one test. The stored row holds key ``1``; Plex now
    serves the same identity under ``2``. Before this phase that produced a
    second row and left the first unscored forever."""
    stale = await _row(session, "1")
    stale_id = stale.id
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(
        kind="movie", title="Dune: Part Two", tmdb_id=693134,
        imdb_id="tt15239678", year=2024, rating_key="1",
    )

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    rows = await _items(session)
    assert [row.rating_key for row in rows] == ["2"], "a twin was created"
    assert rows[0].id == stale_id, "a new row was inserted instead of re-keyed"
    assert {item_id for item_id, _ in seen} == {stale_id}, (
        "the renders landed on a different row than the one that was re-keyed"
    )


async def test_the_re_key_writes_one_audit_row_carrying_both_keys(
    session, tmp_path, monkeypatch
):
    """A re-key that is never written down is a mystery the next
    investigation has to re-derive from the shape of the damage."""
    await _row(session, "1")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    (audit,) = await _audits(session)
    assert audit.event_type == pipeline.REKEY_EVENT
    assert audit.payload["old_rating_key"] == "1"
    assert audit.payload["new_rating_key"] == "2"
    assert audit.payload["tmdb_id"] == 693134
    assert audit.payload["library"] == "Movies"


async def test_the_re_key_leaves_every_asset_path_untouched(
    session, tmp_path, monkeypatch
):
    """Nothing moves on disk. ``naming.asset_path`` takes
    ``(config, library, root_folder, art_kind, season, episode)`` and no
    rating key, so a re-key is a database-only change -- which is also why
    ``asset_cleanup`` needs no change and correctly moved nothing while the
    twins accumulated."""
    stale = await _row(session, "1", root_folder="Dune Part Two (2024)")
    before = "/assets/Movies/Dune Part Two (2024)/poster.jpg"
    session.add(Render(
        item_id=stale.id, art_kind="poster", asset_path=before, status="rendered",
    ))
    await session.commit()
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    poster = (
        await session.execute(select(Render).where(Render.art_kind == "poster"))
    ).scalar_one()
    assert poster.asset_path == before, (
        "the re-key rewrote an asset path; it must be a database-only change"
    )


async def test_a_webhook_intent_with_no_rating_key_still_re_keys(
    session, tmp_path, monkeypatch
):
    """The case only an identity-shaped predicate can reach.

    ``intake/arr.py`` leaves ``rating_key`` None by design -- Sonarr and
    Radarr know nothing about Plex -- so the fork warning never fires and a
    key-comparison guard would leave this door wide open. It was one of the
    two silent twin producers.
    """
    stale = await _row(session, "1")
    stale_id = stale.id
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(
        kind="movie", title="Dune: Part Two", tmdb_id=693134, rating_key=None,
    )

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    rows = await _items(session)
    assert len(rows) == 1 and rows[0].id == stale_id
    assert rows[0].rating_key == "2"


# --- the byte-identical law ------------------------------------------------


async def test_an_unchanged_item_is_byte_identical(session, tmp_path, monkeypatch):
    """The hot path. An item whose key has not moved gets the same row, the
    same key, the same render row and NO audit row -- the re-key's whole cost
    on the ordinary pass is one existence check that its own row satisfies."""
    stale = await _row(session, "1")
    stale_id = stale.id
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("1")), [], intent,
    )

    session.expire_all()
    rows = await _items(session)
    assert len(rows) == 1 and rows[0].id == stale_id and rows[0].rating_key == "1"
    assert {item_id for item_id, _ in seen} == {stale_id}
    assert await _audits(session) == [], (
        "an item that did not move must not leave a re-key audit row"
    )


# --- the three refusals ----------------------------------------------------


async def test_a_cross_library_match_is_not_a_re_key(session, tmp_path, monkeypatch):
    """Same ids, same kind, different library. An item that exists in two
    libraries at once (a 4K and an HD copy) must keep two rows; merging them
    would be data loss, and this refusal is what makes ``library`` part of the
    predicate rather than a nicety."""
    await _row(session, "1", library="Movies 4K")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None,
        _FakePlex(_resolved("2", library="Movies")), [], intent,
    )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}
    assert await _audits(session) == []


async def test_an_id_disjoint_match_is_not_a_re_key(session, tmp_path, monkeypatch):
    """No shared external id, so nothing proves these are the same item.

    This is also the shape of the adopted season/episode population:
    ``resolve()`` reports the SHOW's ids for a season or an episode while
    adoption stored each episode's OWN ids, so those rows are id-disjoint by
    construction and are deliberately left to the twin-merge job.
    """
    await _row(session, "1", tmdb_id=111, imdb_id="tt0000111")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=111,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None,
        _FakePlex(_resolved("2", tmdb_id=693134, imdb_id="tt15239678")), [], intent,
    )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}
    assert await _audits(session) == []


async def test_a_kind_mismatch_is_not_a_re_key(session, tmp_path, monkeypatch):
    """A show row and a movie resolution sharing a TMDB number are not one
    item: a GUID number is unique only WITHIN one agent's namespace, and TMDB
    numbers movies and TV separately -- the production crash
    ``tests/test_plex.py`` already guards at the resolver."""
    await _row(session, "1", kind="show", library="Movies", tmdb_id=693134,
               imdb_id=None)
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None,
        _FakePlex(_resolved("2", imdb_id=None)), [], intent,
    )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}
    assert await _audits(session) == []


async def test_a_row_with_no_external_ids_is_never_re_keyed_on_a_title_match(
    session, tmp_path, monkeypatch
):
    """Titles are not identities. A row carrying no external id at all can
    only be matched by title, and a title match is exactly the class of error
    the resolver's own six refusals exist to avoid."""
    await _row(session, "1", tmdb_id=None, tvdb_id=None, imdb_id=None)
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}
    assert await _audits(session) == []


async def test_two_identity_matches_refuse_the_re_key_and_warn(
    session, tmp_path, monkeypatch, caplog
):
    """Ambiguity is the merge job's problem. Picking either row here would
    pick a side at random and leave the other one to fork again next pass."""
    await _row(session, "1")
    await _row(session, "3")
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    with caplog.at_level(logging.WARNING):
        await pipeline.process_item(
            session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
        )

    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "3", "2"}
    assert await _audits(session) == []
    assert any(
        "rows carry that identity" in record.getMessage()
        for record in caplog.records
    )


async def test_a_season_is_not_re_keyed_onto_another_season_of_the_same_show(
    session, tmp_path, monkeypatch
):
    """Every season of one show shares the show's external ids, so without the
    season/episode coordinates in the predicate season 1's row would match
    season 2's resolution -- and with two seasons stored, the exactly-one
    guard would then refuse every season re-key in the library instead of
    making the one right one."""
    await _row(session, "10", kind="season", library="TV Shows", title="A Show",
               tmdb_id=None, tvdb_id=77, imdb_id=None, season_number=1)
    await _row(session, "11", kind="season", library="TV Shows", title="A Show",
               tmdb_id=None, tvdb_id=77, imdb_id=None, season_number=2)
    _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="season", title="A Show", tvdb_id=77,
                          season_number=2, rating_key="11")
    resolved = _resolved(
        "12", kind="season", library="TV Shows", title="A Show",
        season_number=2, tmdb_id=None, tvdb_id=77, imdb_id=None,
        root_folder="A Show (2020)",
    )

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(resolved), [], intent,
    )

    session.expire_all()
    rows = {row.rating_key: row.season_number for row in await _items(session)}
    assert rows == {"10": 1, "12": 2}, (
        "season 2's row should have followed its key and season 1's should not "
        "have moved"
    )


# --- the key is already taken, and the race -------------------------------


async def test_a_taken_key_leaves_the_existing_twin_alone(
    session, tmp_path, monkeypatch
):
    """When the resolved key already has a row, the twin exists already: this
    is the merge job's pair, not a re-key. The runtime does nothing and the
    ordinary upsert writes the row that holds the key, exactly as today."""
    stale = await _row(session, "1")
    twin = await _row(session, "2")
    stale_id, twin_id = stale.id, twin.id
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    await pipeline.process_item(
        session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
    )

    session.expire_all()
    assert {row.id for row in await _items(session)} == {stale_id, twin_id}
    assert {item_id for item_id, _ in seen} == {twin_id}
    assert await _audits(session) == []


async def test_a_lost_race_degrades_to_the_ordinary_upsert(
    session, session_factory, tmp_path, monkeypatch, caplog
):
    """Two workers can resolve one identity at once and race the key.

    ``rating_key`` carries a real unique constraint, so the loser's flush
    raises ``IntegrityError``. It must roll back and continue -- the same
    rollback-then-continue shape ``process_item`` already uses for its
    metadata and refusal paths -- and the job must not fail.

    The competing insert is landed from inside ``_identity_candidates``, which
    is where the lock is taken: that puts the winner's commit exactly between
    the free-key check and the update, which no fixture can reach from
    outside. Same trick as ``tests/test_scheduler_prune_job.py``'s
    ``_ReupsertingPlex``.
    """
    stale = await _row(session, "1")
    stale_id = stale.id
    real_candidates = pipeline._identity_candidates

    async def racing_candidates(inner_session, item):
        rows = await real_candidates(inner_session, item)
        async with session_factory() as other:
            other.add(MediaItem(
                rating_key=item.rating_key, library="Movies", kind="movie",
                title="Winner", tmdb_id=693134, imdb_id="tt15239678",
            ))
            await other.commit()
        return rows

    monkeypatch.setattr(pipeline, "_identity_candidates", racing_candidates)
    seen = _row_level_render_artifact(monkeypatch)
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          rating_key="1")

    with caplog.at_level(logging.WARNING):
        results = await pipeline.process_item(
            session, _config(tmp_path), None, _FakePlex(_resolved("2")), [], intent,
        )

    assert len(results) == 2, "the job must complete, not fail, on a lost race"
    session.expire_all()
    assert {row.rating_key for row in await _items(session)} == {"1", "2"}, (
        "the loser must not have duplicated the key"
    )
    assert stale_id not in {item_id for item_id, _ in seen}, (
        "the pass must have upserted onto the winner's row"
    )
    assert await _audits(session) == [], (
        "a rolled-back re-key must leave no audit row"
    )


# --- the end-to-end claim, through the genuine render_artifact -------------


class _Provider:
    """One art candidate for every kind this movie asks for. ``name`` is a
    class attribute on the real clients, so the runtime provider rank reads
    exactly this."""

    name = "TMDB"

    async def fetch(self, request):
        if request.art_kind in ("poster", "background"):
            return [ArtCandidate(
                self.name, "https://img/art.jpg", "en", 2000, 3000, 5.0
            )]
        return []


def _fake_http():
    async def handler(request):
        # A decodable PNG, not a placeholder: ``pipeline._download`` decodes
        # every body it keeps (see conftest.decodable_png).
        return httpx.Response(200, content=decodable_png())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_the_real_render_scores_the_re_keyed_row(
    session, tmp_path, monkeypatch
):
    """The claim the whole phase exists to make, with nothing stubbed between
    ``process_item`` and the render row: after a re-key the render is written
    and SCORED on the original row, so the Action Center's unscored floor
    actually falls."""
    stale = await _row(session, "1")
    stale_id = stale.id
    monkeypatch.setattr(pipeline.compositor, "run", lambda argv: None)
    monkeypatch.setattr(
        pipeline, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                          imdb_id="tt15239678", year=2024, rating_key="1")

    async with _fake_http() as http:
        await pipeline.process_item(
            session, _config(tmp_path), http, _FakePlex(_resolved("2")),
            [_Provider()], intent,
        )

    session.expire_all()
    assert [row.id for row in await _items(session)] == [stale_id]
    renders = (
        await session.execute(select(Render).order_by(Render.art_kind))
    ).scalars().all()
    assert renders, "the pass produced no render row at all"
    assert {render.item_id for render in renders} == {stale_id}
    assert all(render.quality_scored_at is not None for render in renders), (
        "the re-keyed row's renders were not scored -- the unscored floor "
        "would not move"
    )
```

- [ ] **Step 5: Run the new tests and watch them fail**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-red1 test sh -c \
  "set -o pipefail; pytest tests/test_pipeline_rekey.py -q 2>&1 | tee /app/.superpowers/run-pirk1-red1.log"
```

Expected: collection succeeds and the tests fail in exactly two shapes — `AttributeError: module 'autoposter.render.pipeline' has no attribute 'REKEY_SOURCE'` (raised inside `_audits`, and inside `monkeypatch.setattr(..., "_identity_candidates", ...)` for the race test), and assertion failures showing a second row where one was expected. **A failure of any other shape means the harness is wrong, not the feature — fix the harness before implementing.** Then `docker rm pirk1-red1`.

- [ ] **Step 6: Implement the re-key in `src/autoposter/render/pipeline.py`**

First, widen the imports at `:11-13` and `:24`:

```python
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
```

```python
from autoposter.db.models import EventLog, ItemFacts, MediaItem, Render
```

Then insert this block **immediately above** `async def _upsert_media_item` (currently `:595`):

```python
# The events_log identity of a re-key, for the reason scheduler/prune.py's
# PRUNE_SOURCE/PRUNE_EVENT are constants: the audit row is the only surviving
# record that a row's Plex key moved under it, and a typo in either would make
# a library's worth of them unfindable.
REKEY_SOURCE = "rekey"
REKEY_EVENT = "media_item_rekeyed"


def _identity_clauses(item: ResolvedItem) -> list:
    """The external-id half of the identity predicate, as OR-able clauses.

    Empty when the resolved item carries no external id at all. That is the
    "never a title-only match" rule expressed as an absence rather than as a
    special case: with no clause to OR there is no identity to match on, and
    every caller reads the empty list as a refusal.
    """
    clauses = []
    if item.tmdb_id is not None:
        clauses.append(MediaItem.tmdb_id == item.tmdb_id)
    if item.tvdb_id is not None:
        clauses.append(MediaItem.tvdb_id == item.tvdb_id)
    if item.imdb_id is not None:
        clauses.append(MediaItem.imdb_id == item.imdb_id)
    return clauses


async def _identity_candidates(
    session: AsyncSession, item: ResolvedItem
) -> list[MediaItem]:
    """Every row carrying ``item``'s identity under some OTHER key, locked.

    Its own function for two reasons. It is where the ``FOR UPDATE`` lives,
    and a lock taken half-way down a longer function is the kind of thing that
    gets moved by accident; and it is the seam the lost-race test wraps, which
    needs somewhere to land a competing insert between the free-key check and
    the update.

    The predicate is the same cross-check ``_fetch_by_rating_key_sync``
    already applies, and adds no new field: same kind, same library, the same
    season/episode coordinates, and at least one external id in common. The
    coordinates are not decoration -- every season of one show carries the
    show's ids, so without them season 1's row matches season 2's resolution
    and the exactly-one guard below refuses every season in the library
    instead of making the one right re-key.

    ``LIMIT 2`` because the caller only ever asks "exactly one?". A third row
    changes no decision and a library-sized result is not worth reading to
    find that out.
    """
    clauses = _identity_clauses(item)
    if not clauses:
        return []
    return (
        await session.execute(
            select(MediaItem)
            .where(MediaItem.kind == item.kind)
            .where(MediaItem.library == item.library)
            .where(MediaItem.season_number.is_not_distinct_from(item.season_number))
            .where(MediaItem.episode_number.is_not_distinct_from(item.episode_number))
            .where(or_(*clauses))
            .where(MediaItem.rating_key != item.rating_key)
            .order_by(MediaItem.id)
            .limit(2)
            .with_for_update()
        )
    ).scalars().all()


async def _rekey_by_identity(
    session: AsyncSession, item: ResolvedItem
) -> str | None:
    """Move an existing row onto ``item``'s live rating key. Returns the old key.

    ``media_items`` is keyed on the Plex rating key and the Plex rating key is
    a *hint*: ``resolve()`` returns the key it FOUND, which after a re-match
    or a library rebuild is not the key the row holds. ``_upsert_media_item``
    below is an ``ON CONFLICT (rating_key)`` upsert, so a new key conflicts
    with nothing and INSERTS -- a second row that inherits every future render
    while the original keeps its renders, its facts, its dismissals, its
    ``logo_upload_key`` and its children and is never written again.

    The precondition is proved from the DATABASE, not from a key comparison,
    and that is the whole design. Comparing ``intent.rating_key`` to
    ``item.rating_key`` covers only the paths that CARRY a key; the ratings
    drift sweep and the Sonarr/Radarr webhooks carry none by construction, and
    both minted twins with no warning at all. Two facts, in this order:

    1. **No row already holds the resolved key.** If one does, the twin exists
       already, and reconciling two rows is the ``plex_merge`` job's work
       under a dry run an operator reads first -- so this does nothing. This
       check is also the hot path's entire cost: an item whose key has not
       moved satisfies it with its own row and returns immediately.
    2. **Exactly one row carries the resolved identity.** Zero is an ordinary
       new item; more than one is an ambiguity that would otherwise be settled
       by picking a side at random.

    Concurrency: ``rating_key`` carries a real unique constraint, which is why
    ``_upsert_media_item`` is an upsert in the first place. Two workers can
    resolve the same identity at once, so the candidate is taken ``FOR
    UPDATE`` and the flush is guarded -- the loser rolls back and falls
    through to the ordinary upsert, which then finds the row the winner
    created. A lost race must never fail a job.

    The write is committed rather than left to the caller's transaction. A
    ``process_item`` whose every art kind refuses raises and the worker rolls
    back, so a flush-only re-key would be lost -- and the same fork would then
    happen again on every subsequent pass while the audit trail said it had
    been fixed.
    """
    taken = (
        await session.execute(
            select(MediaItem.id).where(MediaItem.rating_key == item.rating_key)
        )
    ).scalar_one_or_none()
    if taken is not None:
        return None

    candidates = await _identity_candidates(session, item)
    if len(candidates) != 1:
        if candidates:
            logger.warning(
                "not re-keying to %s: %d rows carry that identity "
                "(%s in %r); the twin merge owns this pair",
                item.rating_key, len(candidates), item.kind, item.library,
            )
        return None

    stale = candidates[0]
    stale_id = stale.id
    old_key = stale.rating_key
    try:
        # An ORM mutation rather than a Core UPDATE: the row is in this
        # session's identity map (the SELECT above loaded it), and a Core
        # UPDATE with synchronize_session=False would leave the in-memory
        # object still reporting the OLD key -- which is exactly what
        # _upsert_media_item's re-SELECT would then hand back.
        stale.rating_key = item.rating_key
        await session.flush()
        session.add(EventLog(
            source=REKEY_SOURCE,
            event_type=REKEY_EVENT,
            payload={
                "media_item_id": stale_id,
                "old_rating_key": old_key,
                "new_rating_key": item.rating_key,
                "kind": item.kind,
                "library": item.library,
                "title": item.title,
                "season_number": item.season_number,
                "episode_number": item.episode_number,
                "tmdb_id": item.tmdb_id,
                "tvdb_id": item.tvdb_id,
                "imdb_id": item.imdb_id,
            },
            outcome=f"re-keyed {old_key} -> {item.rating_key} on an identity match",
        ))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.warning(
            "re-key of %s to %s lost a race; the winner's row holds the key "
            "and this pass will upsert onto it",
            old_key, item.rating_key, exc_info=True,
        )
        return None

    logger.info(
        "re-keyed media_items row %d from %s to %s (%s %r)",
        stale_id, old_key, item.rating_key, item.kind, item.title,
    )
    return old_key
```

Finally, in `process_item`, insert the call **between** the fork WARNING block (which currently ends at `:1491`) and `media_item = None` (currently `:1493`):

```python
    # The re-key. The WARNING above only fires when the intent CARRIED a key,
    # so it can never see the two silent producers -- the ratings-drift sweep
    # and the webhook intake both build intents with no rating key at all --
    # and a key-comparison guard here would leave both open. This asks the
    # database instead, which closes every upserting path in one place: is the
    # resolved key free, and does exactly one row carry the resolved identity.
    # It must run BEFORE the first _upsert_media_item below, because that is
    # the call that would otherwise insert the twin (and render_artifact, the
    # refusal path and the badge path all upsert again after it).
    #
    # The return value is deliberately unused: the durable record of a re-key
    # is its events_log row, not a local.
    await _rekey_by_identity(session, item)
```

- [ ] **Step 7: Run the tests and watch them pass**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-green1 test sh -c \
  "set -o pipefail; pytest tests/test_pipeline_rekey.py tests/test_pipeline.py tests/test_pipeline_facts.py tests/test_pipeline_quality.py tests/test_pipeline_e2e.py tests/test_worker.py -q 2>&1 | tee /app/.superpowers/run-pirk1-green1.log"
```

Expected: all pass. The existing pipeline and worker suites run alongside because the new call sits on their hot path — a regression there is the one failure this step must not hide. Then `docker rm pirk1-green1`.

- [ ] **Step 8: Lint**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-ruff test sh -c "ruff check ."
```

Expected: `All checks passed!`. Then `docker rm pirk1-ruff`.

- [ ] **Step 9: Commit**

```bash
cd /d/Sites/autoposter && \
  git add src/autoposter/render/pipeline.py tests/test_pipeline_rekey.py && \
  git commit -m "fix(pipeline): re-key a re-matched item's own row instead of forking it"
```

- [ ] **Step 10: Write the failing drift-sweep test**

Append to `tests/test_scheduler_drift_job.py`:

```python
async def test_the_enqueued_intent_carries_the_rows_rating_key(session):
    """The first of the two silent twin producers.

    ``_stamp_and_enqueue`` built its ``RenderIntent`` with no ``rating_key``
    field at all, so every drift job went straight to the GUID walk, resolved
    the LIVE key and upserted a second row -- silently, because the pipeline's
    fork warning only fires when the intent carried a key to disagree with.
    Carrying the key is what every other row-derived intent already does
    (``prune.intent_for``, ``routes._enqueue_reprocess``,
    ``action_center._reprocess_entries``).
    """
    item = await _make_item(session, title="Stale Movie", tmdb_id=42)
    await _make_facts(session, item.id, age_days=8)

    count = await sweep_stale_facts(session, max_age_days=7, batch_size=500)

    assert count == 1
    (job,) = (await session.execute(select(Job).order_by(Job.id))).scalars().all()
    assert job.payload["rating_key"] == item.rating_key
```

- [ ] **Step 11: Run it and watch it fail**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-red2 test sh -c \
  "set -o pipefail; pytest tests/test_scheduler_drift_job.py::test_the_enqueued_intent_carries_the_rows_rating_key -q 2>&1 | tee /app/.superpowers/run-pirk1-red2.log"
```

Expected: FAIL on the last assertion — `asdict(intent)` does carry the key `rating_key`, but its value is `None`, so the failure reads `assert None == '<the row's key>'`. Then `docker rm pirk1-red2`.

- [ ] **Step 12: Carry the key in `src/autoposter/scheduler/jobs.py`**

In `_stamp_and_enqueue`, replace the `RenderIntent(...)` construction (currently `:240-249`) with:

```python
        intent = RenderIntent(
            kind=item.kind,
            title=item.title,
            tmdb_id=item.tmdb_id,
            tvdb_id=item.tvdb_id,
            imdb_id=item.imdb_id,
            year=item.year,
            season_number=item.season_number,
            episode_number=item.episode_number,
            # The row's own Plex identity, like every other row-derived intent
            # in the tree. Without it this sweep was one of the two SILENT
            # twin producers: no key means no rating-key hint, so every drift
            # job took the GUID walk, resolved the live key and upserted a
            # second row -- and the pipeline's fork warning never fired,
            # because it only fires when the intent carried a key to disagree
            # with. The pipeline's identity re-key closes the hole either way;
            # carrying the key also makes the fork visible in the log.
            rating_key=item.rating_key,
        )
```

- [ ] **Step 13: Run the drift suite green**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-green2 test sh -c \
  "set -o pipefail; pytest tests/test_scheduler_drift_job.py tests/test_api_facts_backfill.py -q 2>&1 | tee /app/.superpowers/run-pirk1-green2.log"
```

Expected: all pass. `backfill_facts` shares `_stamp_and_enqueue`, so its own suite runs here too. (If `tests/test_api_facts_backfill.py` does not exist under that name, run `pytest tests/ -k facts_backfill -q` instead and record which file answered.) Then `docker rm pirk1-green2`.

- [ ] **Step 14: Commit**

```bash
cd /d/Sites/autoposter && \
  git add src/autoposter/scheduler/jobs.py tests/test_scheduler_drift_job.py && \
  git commit -m "fix(drift): the sweep's intents carry the row's rating key"
```

- [ ] **Step 15: Write the failing discovery tests**

Append to `tests/test_arr_safety_net.py`:

```python
async def test_an_identity_already_stored_under_another_key_is_enqueued_from_that_row(
    session,
):
    """Discovery's anti-join is on the KEY, so a re-matched item's live key is
    absent from ``media_items`` (only its stale key is there), the item reads
    as "unknown", and it is enqueued with the LIVE key -- which
    ``_fetch_by_rating_key_sync`` accepts, so no fork occurs, no warning
    fires, and ``_upsert_media_item`` inserts a twin. Running per section over
    whole libraries, that made this the largest twin producer in the tree.

    Enqueuing the intent built from the EXISTING ROW instead turns it from a
    producer into a repairer: the job forks, the pipeline's identity re-key
    fires, and the item is fixed rather than duplicated.
    """
    session.add(MediaItem(
        rating_key="900", library="Movies", kind="movie", title="Old Title",
        tmdb_id=438631, year=2021,
    ))
    await session.commit()

    items = [FakeItem("901", "Dune", ["tmdb://438631"])]
    count = await enqueue_unknown_items(session, items, "movie")

    assert count == 1
    jobs = await _pending_jobs(session)
    assert jobs[0].payload["rating_key"] == "900", (
        "discovery enqueued the live key and would have minted a twin"
    )
    assert jobs[0].payload["title"] == "Old Title"


async def test_two_rows_for_one_identity_fall_back_to_the_live_key(session):
    """Ambiguity picks no side. Two rows carrying one identity is the twin
    merge's pair; enqueuing either one's key here would choose at random, so
    discovery does exactly what it does today and leaves the pair alone."""
    for key in ("900", "902"):
        session.add(MediaItem(
            rating_key=key, library="Movies", kind="movie", title="Old Title",
            tmdb_id=438631, year=2021,
        ))
    await session.commit()

    items = [FakeItem("901", "Dune", ["tmdb://438631"])]
    count = await enqueue_unknown_items(session, items, "movie")

    assert count == 1
    jobs = await _pending_jobs(session)
    assert jobs[0].payload["rating_key"] == "901"
    assert jobs[0].payload["title"] == "Dune"
```

- [ ] **Step 16: Run them and watch the first fail**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-red3 test sh -c \
  "set -o pipefail; pytest tests/test_arr_safety_net.py -q 2>&1 | tee /app/.superpowers/run-pirk1-red3.log"
```

Expected: `test_an_identity_already_stored_under_another_key_is_enqueued_from_that_row` FAILS with `assert '901' == '900'`; `test_two_rows_for_one_identity_fall_back_to_the_live_key` already PASSES — it pins today's behaviour, which the change must preserve. Then `docker rm pirk1-red3`.

- [ ] **Step 17: Implement the discovery repair in `src/autoposter/arr/sync.py`**

Widen the import at `:17`:

```python
from sqlalchemy import or_, select
```

Add this helper immediately **above** `async def enqueue_unknown_items` (currently `:321`):

```python
async def _stale_rows_by_key(
    session: AsyncSession, kind: str, guids_by_key: dict[str, dict]
) -> dict[str, MediaItem]:
    """For each unknown Plex key, the ONE row already carrying its identity.

    One query for the whole section, never one per item: the anti-join in
    ``enqueue_unknown_items`` is a single query by design and this must not
    undo that.

    Deliberately NARROWER than the pipeline's own re-key predicate, which also
    demands the same library. All this decides is WHICH intent to enqueue; the
    pipeline re-checks the full predicate when the job runs, so a wrong guess
    here costs exactly what today's code costs -- a job that forks and creates
    a twin -- and can never cause a wrong re-key. The season/episode columns
    are pinned NULL because this sweep only ever walks movie and show
    sections.

    Exactly one match or nothing: two rows carrying one identity is the
    ``plex_merge`` job's pair, and enqueuing either one's key would pick a
    side at random.
    """
    tmdb = {as_int(g.get("tmdb")) for g in guids_by_key.values()} - {None}
    tvdb = {as_int(g.get("tvdb")) for g in guids_by_key.values()} - {None}
    imdb = {g.get("imdb") for g in guids_by_key.values()} - {None}
    clauses = []
    if tmdb:
        clauses.append(MediaItem.tmdb_id.in_(tmdb))
    if tvdb:
        clauses.append(MediaItem.tvdb_id.in_(tvdb))
    if imdb:
        clauses.append(MediaItem.imdb_id.in_(imdb))
    if not clauses:
        return {}

    rows = (
        await session.execute(
            select(MediaItem)
            .where(MediaItem.kind == kind)
            .where(MediaItem.season_number.is_(None))
            .where(MediaItem.episode_number.is_(None))
            .where(or_(*clauses))
            .order_by(MediaItem.id)
        )
    ).scalars().all()

    by_tmdb: dict[int, list[MediaItem]] = {}
    by_tvdb: dict[int, list[MediaItem]] = {}
    by_imdb: dict[str, list[MediaItem]] = {}
    for row in rows:
        if row.tmdb_id is not None:
            by_tmdb.setdefault(row.tmdb_id, []).append(row)
        if row.tvdb_id is not None:
            by_tvdb.setdefault(row.tvdb_id, []).append(row)
        if row.imdb_id is not None:
            by_imdb.setdefault(row.imdb_id, []).append(row)

    matched: dict[str, MediaItem] = {}
    for key, guids in guids_by_key.items():
        found: list[MediaItem] = []
        for row in (
            by_tmdb.get(as_int(guids.get("tmdb")), [])
            + by_tvdb.get(as_int(guids.get("tvdb")), [])
            + by_imdb.get(guids.get("imdb"), [])
        ):
            if row not in found:
                found.append(row)
        if len(found) == 1:
            matched[key] = found[0]
    return matched
```

`AsyncSession` is already imported in this module (`:18`). Then replace the body of `enqueue_unknown_items` from `if not items:` (currently `:343`) to the end of the function with:

```python
    if not items:
        return 0

    rating_keys = [str(item.ratingKey) for item in items]
    known = set(
        (
            await session.execute(
                select(MediaItem.rating_key).where(MediaItem.rating_key.in_(rating_keys))
            )
        ).scalars()
    )

    guids_by_key = {
        str(item.ratingKey): parse_guids(
            [g.id for g in getattr(item, "guids", None) or []]
        )
        for item in items
        if str(item.ratingKey) not in known
    }
    stale_by_key = await _stale_rows_by_key(session, kind, guids_by_key)

    enqueued = 0
    for item in items:
        if enqueued >= batch_size:
            break
        key = str(item.ratingKey)
        if key in known:
            continue

        stale = stale_by_key.get(key)
        if stale is not None:
            # This item's identity already HAS a row, under a different key --
            # it was re-matched or renumbered and its row never followed. The
            # anti-join above cannot see that, because it compares keys, so
            # this used to read as "unknown" and enqueue the LIVE key, which
            # the resolver accepts without forking and which therefore upserts
            # a second row with nothing to warn about. Enqueuing the STALE
            # row's intent instead makes the job fork, which is what lets the
            # pipeline's identity re-key repair the row rather than duplicate
            # it.
            intent = RenderIntent(
                kind=kind,
                title=stale.title,
                tmdb_id=stale.tmdb_id,
                tvdb_id=stale.tvdb_id,
                imdb_id=stale.imdb_id,
                year=stale.year,
                rating_key=stale.rating_key,
            )
        else:
            guids = guids_by_key[key]
            intent = RenderIntent(
                kind=kind,
                title=item.title,
                tmdb_id=as_int(guids.get("tmdb")),
                tvdb_id=as_int(guids.get("tvdb")),
                imdb_id=guids.get("imdb"),
                year=getattr(item, "year", None),
                rating_key=key,
            )
        job_id = await enqueue(
            session, kind="process_item", payload=asdict(intent), dedupe_key=intent.dedupe_key,
        )
        if job_id is not None:
            enqueued += 1

    return enqueued
```

- [ ] **Step 18: Run the discovery and arr suites green**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-green3 test sh -c \
  "set -o pipefail; pytest tests/test_arr_safety_net.py tests/test_arr_sync.py tests/test_scheduler_arr_sync_job.py -q 2>&1 | tee /app/.superpowers/run-pirk1-green3.log"
```

Expected: all pass. Then `docker rm pirk1-green3`.

- [ ] **Step 19: Commit**

```bash
cd /d/Sites/autoposter && \
  git add src/autoposter/arr/sync.py tests/test_arr_safety_net.py && \
  git commit -m "fix(arr-sync): discovery enqueues the existing row when the key moved"
```

- [ ] **Step 20: Full suite, lint, and the read-only check**

```bash
docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk1-full test sh -c \
  "set -o pipefail; ruff check . && pytest -q 2>&1 | tee /app/.superpowers/run-pirk1-full.log"
```

Expected: `ruff` clean, and a green run whose total is the Step 3 baseline **plus 17** (14 in `tests/test_pipeline_rekey.py`, 1 in the drift suite, 2 in the safety-net suite). Report the delta against the measured baseline, not against any figure in this document.

```bash
cd /d/Sites/autoposter && git diff --stat <the Step 2 sha> -- src/ tests/ docs/
```

Expected: exactly the six files named in this task's **Files** block and nothing else.

Tear down:

```bash
docker rm pirk1-full && \
  docker compose -p pirk1 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## Task 2: The `plex_merge` maintenance job

**Files:**
- Modify: `src/autoposter/plex/client.py` (`_key_resolves_sync` / `keys_resolve`, beside `_exists_sync` / `exists_many` at `:618-650`)
- Create: `src/autoposter/scheduler/merge.py`
- Modify: `src/autoposter/config/schema.py` (`MergeConfig` after `PruneConfig` at `:2110-2153`; `Config.merge` beside `Config.prune` at `:2580`; `SchedulerConfig.merge_days` beside `prune_days` at `:2311`)
- Modify: `config/autoposter.example.yaml` (a `merge:` block after `prune:` at `:303-310`)
- Modify: `src/autoposter/api/routes.py:93-102` (`"plex_merge"` in `SCHEDULED_JOB_NAMES`)
- Modify: `src/autoposter/app.py` (`make_merge_job` registered beside `make_prune_job` at `:334-340`)
- Create: `tests/test_scheduler_merge_job.py`
- Modify: `tests/test_plex.py` (two `keys_resolve` pins)
- Modify: `tests/test_app.py` (one boot-registration pin)
- Modify: `tests/test_api_scheduled_runs.py` (one Run-now pin)

**Interfaces:**
- Consumes from T1: nothing at runtime — T2 is the backlog's cure and T1 is its prevention. It consumes only shipped code: `scheduler.prune.dismiss_jobs_for(session, rating_keys) -> int`, `artwork_modes.base.refuse_if_empty` / `SHARE_CHECK_MIN_ITEMS`, `scheduler.core.Job`, `config.holder.ConfigHolder`.
- Produces, for T3:
  - `plex.client.PlexClient.keys_resolve(intents: list[RenderIntent]) -> list[bool]` — whether each intent's *stored key* is accepted, with no GUID-walk fallback.
  - `scheduler.merge.MERGE_SOURCE = "merge"`, `MERGE_EVENT = "media_items_merged"`, `class MergeRefused(Exception)` (`served_detail = True`).
  - `scheduler.merge.MergeRow`, `MergePair`, `MergePlan`, `MergeScan`, `MergeOutcome`, `ProbeResult` (frozen dataclasses).
  - `async find_mergeable(session) -> MergeScan`, `async verify_survivors(plex, plans) -> ProbeResult`, `async merge(session, plans) -> MergeOutcome`, `implausible_merge_count(pairs, total, config) -> str | None`, `intent_for_row(row) -> RenderIntent`, `make_merge_job(holder, plex_factory, is_healthy) -> Job` with `Job.name == "plex_merge"`.

---

- [ ] **Step 1: Write the failing `keys_resolve` tests**

The survivor election needs to know whether a row's **own stored key** is still accepted — not whether *something* with those ids exists. `exists_many` cannot answer that: it shares `_search_sync` with `resolve`, which falls through to the GUID walk on any refusal, so a re-keyed item resolves either way and both rows of a twin pair would read "live". Add the key-level probe.

Append to `tests/test_plex.py`:

```python
async def test_keys_resolve_answers_only_for_the_stored_key(server):
    """The twin merge's survivor election, which ``exists_many`` cannot make.

    ``exists_many`` shares ``_search_sync`` with ``resolve``, so it falls
    through to the GUID walk on any rating-key refusal -- a re-matched item
    "exists" under both its stale key and its live one, and the election
    between two twin rows would be a coin toss. ``keys_resolve`` asks
    ``_fetch_by_rating_key_sync`` and nothing else: it answers whether THIS
    row's stored key is still the item's key.
    """
    movie = FakeItem(
        "12345", "Dune: Part Two", 2024,
        "/mnt/Media/Movies/Dune Part Two (2024)/dune.mkv",
        ["tmdb://693134", "imdb://tt15239678"], library_section_title="Movies",
    )
    movies = FakeSection("Movies", "/mnt/Media/Movies", [movie])
    live_server = FakeServer([movies], items_by_key={12345: movie})
    client = PlexClient(server=live_server, excluded_libraries=[])

    live = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                        rating_key="12345")
    stale = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134,
                         rating_key="999")

    assert await client.keys_resolve([live, stale]) == [True, False]
    assert movies.getguid_calls == [], (
        "keys_resolve must never fall through to the GUID walk -- that is the "
        "very fallback it exists to bypass"
    )


async def test_keys_resolve_refuses_an_intent_with_no_stored_key(server):
    """No key means nothing to accept. A webhook-born intent carries none by
    design, and answering True for it would elect a survivor on no evidence."""
    client = PlexClient(server=server, excluded_libraries=["Photos"])
    intent = RenderIntent(kind="movie", title="Dune: Part Two", tmdb_id=693134)

    assert await client.keys_resolve([intent]) == [False]
    assert await client.keys_resolve([]) == []
```

- [ ] **Step 2: Run them and watch them fail**

```bash
docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk2-red1 test sh -c \
  "set -o pipefail; pytest tests/test_plex.py -q -k keys_resolve 2>&1 | tee /app/.superpowers/run-pirk2-red1.log"
```

Expected: FAIL with `AttributeError: 'PlexClient' object has no attribute 'keys_resolve'`. Then `docker rm pirk2-red1`.

- [ ] **Step 3: Implement `keys_resolve` in `src/autoposter/plex/client.py`**

Insert immediately **after** `exists_many` (which currently ends at `:650`) and before `async def resolve`:

```python
    def _key_resolves_sync(self, intent: RenderIntent) -> bool:
        """Whether ``intent.rating_key`` is still the item's own key.

        The wanted-type split is ``_search_sync``'s, verbatim: a movie intent
        can only be a movie; show, season and episode intents all resolve
        through a show library.
        """
        if not intent.rating_key:
            return False
        wanted_type = "movie" if intent.kind == "movie" else "show"
        sections = self._sections(wanted_type)
        return self._fetch_by_rating_key_sync(intent, sections) is not None

    async def keys_resolve(self, intents: list[RenderIntent]) -> list[bool]:
        """Whether each intent's STORED KEY is still accepted, in the order given.

        The twin merge's survivor election, and deliberately NOT
        ``exists_many``. That method shares ``_search_sync`` with ``resolve``,
        which falls through to the GUID walk on any rating-key refusal -- so a
        re-matched item reads as present under BOTH its stale key and its live
        one, and an election between two rows carrying one identity would be a
        coin toss. This asks ``_fetch_by_rating_key_sync`` and stops there: it
        answers "is this row's key the item's key", which is exactly the
        question that decides which of a twin pair survives.

        One thread for the whole walk, like ``exists_many`` and for the same
        reason: this loop shares the event loop with the worker pool and the
        Plex liveness probe.

        Nothing is caught. A probe that fails for any reason other than "not
        found" must reach the caller, because a merge that read an error as a
        refusal would delete the wrong row of the pair.
        """

        def _walk() -> list[bool]:
            return [self._key_resolves_sync(intent) for intent in intents]

        return await asyncio.to_thread(_walk)
```

- [ ] **Step 4: Run the Plex suite green**

```bash
docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk2-green1 test sh -c \
  "set -o pipefail; pytest tests/test_plex.py -q 2>&1 | tee /app/.superpowers/run-pirk2-green1.log"
```

Expected: all pass. Then `docker rm pirk2-green1`.

- [ ] **Step 5: Commit**

```bash
cd /d/Sites/autoposter && \
  git add src/autoposter/plex/client.py tests/test_plex.py && \
  git commit -m "feat(plex): keys_resolve, a key-level probe with no GUID fallback"
```

- [ ] **Step 6: Add the config surface**

In `src/autoposter/config/schema.py`, add `MergeConfig` immediately **after** `PruneConfig` (which currently ends at `:2153`, before `class AdoptConfig`):

```python
class MergeConfig(BaseModel):
    """Merging the twin ``media_items`` rows a re-key was too late to prevent.
    See ``scheduler/merge.py``.

    Dry run by default, the same posture as ``prune.apply`` and
    ``cleanup.apply``, and for the stricter of the two reasons: this pass
    deletes a row. What makes it safer than the prune is that the row's
    contents are not lost -- renders, facts, credits, dismissals and children
    are repointed onto the surviving twin first -- but a wrong pair is still
    two items collapsed into one, so it stays off until a dry-run report reads
    the way an operator expects.

    The dry run is probe-free on purpose: it elects the survivor by newest
    rating key and touches no Plex at all, so the report can be read during an
    outage. The APPLIED pass probes both rows by key and merges only the pair
    where the survivor's key is accepted and the stale one's is not.
    """

    apply: bool = Field(
        default=False,
        description="Actually merge twin media_items rows; off only reports which pairs would merge.",
    )
    # Sanity caps mirroring prune.max_prunes / max_prune_share. The failure
    # they catch is different from the prune's, and worse: an identity
    # predicate that is wrong -- a library rename that makes two libraries
    # read as one, an import that duplicated external ids -- makes a large
    # part of the library look like twins at once, and every merge deletes a
    # row. Past either cap the pass refuses and reports the numbers.
    max_merges: int = Field(
        default=500,
        description="Refuse a pass whose twin-pair count exceeds this, and report the numbers instead of merging anything.",
    )
    max_merge_share: float = Field(
        default=0.25,
        description=(
            "Refuse a pass whose twin-pair count exceeds this share of the "
            "library, and report the numbers instead of merging anything."
        ),
    )
```

In `SchedulerConfig`, add immediately **after** `prune_days` (currently `:2308-2311`):

```python
    merge_days: int = Field(
        default=7,
        description="How often the media_items twin merge runs.",
    )
```

In `Config`, add immediately **after** the `prune:` field (currently `:2580`), matching the surrounding `Field(...)` shape used by `cleanup`/`prune`:

```python
    merge: MergeConfig = Field(
        default_factory=MergeConfig,
        description="Merging twin media_items rows left by re-matched items.",
    )
```

In `config/autoposter.example.yaml`, add after the `prune:` block (currently ending `:310`):

```yaml
merge:
  # Reconciles the pairs of media_items rows a re-matched item left behind
  # before the pipeline learned to re-key: renders, facts, credits,
  # dismissals and child rows move onto the surviving twin, then the stale
  # row is deleted. Files are never touched -- both rows record the same
  # asset paths. The dry run needs no Plex at all; the applied pass verifies
  # each survivor against Plex before deleting its twin.
  apply: false # dry run by default: report which pairs would merge, delete nothing
  max_merges: 500 # refuse the pass if more than this many twin pairs are found
  max_merge_share: 0.25 # ...or more than this share of the library; that means the identity predicate is matching something it should not
```

Add `merge_days: 7` beside `prune_days` in the example's `scheduler:` block if that block lists the other cadences; if it does not list `prune_days`, add nothing (the example only documents what it already documents).

- [ ] **Step 7: Check the config surface holds**

```bash
docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk2-config test sh -c \
  "set -o pipefail; pytest tests/test_example_config_matches_schema.py tests/test_scheduler_config.py tests/test_api_config_editor.py -q 2>&1 | tee /app/.superpowers/run-pirk2-config.log"
```

Expected: all pass — every key in the example exists in the schema. Then `docker rm pirk2-config`.

- [ ] **Step 8: Write the failing merge-job tests**

Create `tests/test_scheduler_merge_job.py`:

```python
"""``find_mergeable`` / ``merge`` / ``make_merge_job``: the twin-row merge.

Before the pipeline learned to re-key, a re-matched item produced a SECOND
``media_items`` row under the live key. The original kept its render rows, its
facts, its credits, its dismissals, its ``logo_upload_key`` and its children,
and was never written again. This job reconciles those pairs.

The three things worth knowing before changing anything here:

* ``media_items.parent_id`` cascades, so deleting the stale row of a show pair
  would take its seasons and episodes with it -- children are repointed onto
  the survivor BEFORE the delete, in the same transaction, and that ordering
  is the single most dangerous thing in this module;
* the dry run is PROBE-FREE by design (it elects the survivor by newest rating
  key) so the report can be read while Plex is down, and the applied pass
  probes both rows by key before it deletes anything;
* a repointed dismissal usually re-surfaces, because ``action_dismissals``
  keys on a hash of the render facts and the survivor is scored where the
  stale row was not. That is the dismissal contract working, not a bug, and
  the summary says so.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from autoposter.config.holder import ConfigHolder
from autoposter.db.models import (
    ActionDismissal, EventLog, ItemCredit, ItemFacts, Job, MediaItem, Render,
)
from autoposter.plex.client import ResolvedItem
from autoposter.render.pipeline import _upsert_media_item
from autoposter.scheduler.merge import (
    MERGE_EVENT,
    MERGE_SOURCE,
    MergeRefused,
    find_mergeable,
    implausible_merge_count,
    intent_for_row,
    make_merge_job,
    merge,
    verify_survivors,
)


class FakePlex:
    """A ``PlexClient`` stand-in answering only ``keys_resolve``.

    ``live`` is the set of rating keys whose OWN key Plex still accepts --
    which is the whole question the survivor election asks, and the reason
    this double does not implement ``exists_many`` at all. ``error``, when
    set, is raised instead: "the probe failed" and "the key is refused" must
    never be the same answer.
    """

    def __init__(self, live=(), *, error=None):
        self._live = set(live)
        self._error = error
        self.asked = []

    async def keys_resolve(self, intents):
        if self._error is not None:
            raise self._error
        self.asked.extend(intents)
        return [intent.rating_key in self._live for intent in intents]


async def _item(session, rating_key, **columns):
    """One committed ``media_items`` row. Committed so ``updated_at`` is a
    settled value from its own transaction -- half the delete's key."""
    fields = dict(
        library="Movies", kind="movie", title="Dune: Part Two", year=2024,
        tmdb_id=693134,
    )
    fields.update(columns)
    item = MediaItem(rating_key=rating_key, **fields)
    session.add(item)
    await session.commit()
    return item


async def _render(session, item_id, art_kind, *, scored=False, path=None):
    render = Render(
        item_id=item_id, art_kind=art_kind, status="rendered",
        asset_path=path or f"/assets/Movies/Dune Part Two (2024)/{art_kind}.jpg",
    )
    session.add(render)
    await session.commit()
    if scored:
        await session.execute(
            Render.__table__.update()
            .where(Render.id == render.id)
            .values(quality_scored_at=func.now())
        )
        await session.commit()
    return render


async def _pair(session, *, stale_key="1", survivor_key="2", **columns):
    """The canonical twin pair: one identity, two keys, survivor is the newer."""
    stale = await _item(session, stale_key, **columns)
    survivor = await _item(session, survivor_key, **columns)
    return stale, survivor


def _config(*, apply=False, max_merges=500, max_merge_share=0.25):
    """Only what the job reads -- the ``tests/test_scheduler_prune_job.py``
    ``_config`` pattern, so each test's intent stays on screen."""
    return SimpleNamespace(
        merge=SimpleNamespace(
            apply=apply, max_merges=max_merges, max_merge_share=max_merge_share
        ),
        scheduler=SimpleNamespace(merge_days=7),
    )


def _job(config, plex, *, healthy=True):
    return make_merge_job(ConfigHolder(config), lambda: plex, lambda: healthy)


# --- finding and electing --------------------------------------------------


async def test_two_rows_with_one_identity_are_a_pair_and_the_newer_key_survives(
    session,
):
    """A5: the dry run elects by newest rating key, which is probe-free, so
    the report runs anywhere -- including with Plex down."""
    await _pair(session, stale_key="16201", survivor_key="165269")

    scan = await find_mergeable(session)

    assert len(scan.plans) == 1
    pair = scan.plans[0].pair
    assert pair.stale.rating_key == "16201"
    assert pair.survivor.rating_key == "165269"
    assert scan.total == 2


async def test_a_lone_row_is_not_a_pair(session):
    await _item(session, "1")

    scan = await find_mergeable(session)

    assert scan.plans == []


async def test_three_rows_for_one_identity_are_ambiguous_and_left_alone(session):
    """Three rows for one item is not three merges, it is one ambiguity. A
    pairwise walk would happily collapse them in an order nobody chose."""
    for key in ("1", "2", "3"):
        await _item(session, key)

    scan = await find_mergeable(session)

    assert scan.plans == []
    assert scan.ambiguous == 1


async def test_a_non_numeric_rating_key_makes_the_pair_unelectable(session):
    """The election compares keys as integers. A key that is not a number
    cannot be ordered, and guessing an order would guess which row dies."""
    await _pair(session, stale_key="1", survivor_key="not-a-number")

    scan = await find_mergeable(session)

    assert scan.plans == []
    assert scan.unelectable == 1


async def test_rows_in_different_libraries_are_not_a_pair(session):
    """An item that exists in two libraries at once -- a 4K copy and an HD one
    -- is two items. Merging them is data loss."""
    await _item(session, "1", library="Movies")
    await _item(session, "2", library="Movies 4K")

    assert (await find_mergeable(session)).plans == []


async def test_rows_with_no_shared_external_id_are_not_a_pair(session):
    await _item(session, "1", tmdb_id=111)
    await _item(session, "2", tmdb_id=222)

    assert (await find_mergeable(session)).plans == []


async def test_two_seasons_of_one_show_are_not_a_pair(session):
    """Every season carries the SHOW's ids, so without the season number in
    the predicate the whole show collapses into one row."""
    common = dict(kind="season", library="TV Shows", title="A Show",
                  tmdb_id=None, tvdb_id=77)
    await _item(session, "10", season_number=1, **common)
    await _item(session, "11", season_number=2, **common)

    assert (await find_mergeable(session)).plans == []


async def test_a_row_with_no_external_ids_is_never_paired(session):
    await _item(session, "1", tmdb_id=None, tvdb_id=None, imdb_id=None)
    await _item(session, "2", tmdb_id=None, tvdb_id=None, imdb_id=None)

    assert (await find_mergeable(session)).plans == []


async def test_the_scan_counts_unscored_rows_with_no_twin_separately(session):
    """A2's population, made visible before any apply. An unscored render on a
    row that has NO identity twin is a different problem -- an adopted
    season/episode whose stored ids are its own, or a genuine miss -- and this
    job must not claim credit for it."""
    lonely = await _item(session, "9", tmdb_id=555)
    await _render(session, lonely.id, "poster")
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, survivor.id, "poster", scored=True)

    scan = await find_mergeable(session)

    assert len(scan.plans) == 1
    assert scan.no_identity_match == 1


async def test_the_scan_counts_pairs_where_neither_row_is_scored(session):
    """The operator's own cross-check: a pair with no scored render on either
    row is a second defect wearing this one's clothes, and the merge must not
    report it as progress."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, survivor.id, "background")

    scan = await find_mergeable(session)

    assert len(scan.plans) == 1
    assert scan.neither_scored == 1


# --- planning --------------------------------------------------------------


async def test_a_kind_the_survivor_lacks_is_planned_for_a_repoint(session):
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, survivor.id, "background")

    (plan,) = (await find_mergeable(session)).plans

    assert plan.renders_repoint == ["poster"]
    assert plan.renders_drop == []


async def test_a_kind_both_rows_hold_is_planned_for_a_drop(session):
    """The survivor's is the one being scored, so the stale row's is the pure
    duplicate. Removing it is what honestly shrinks the denominator."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, survivor.id, "poster", scored=True)

    (plan,) = (await find_mergeable(session)).plans

    assert plan.renders_repoint == []
    assert plan.renders_drop == ["poster"]


# --- applying --------------------------------------------------------------


async def test_a_repointed_render_keeps_its_asset_path(session):
    """Nothing moves on disk: both rows record the SAME path, because
    ``naming.asset_path`` is keyed by library and root folder."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster",
                  path="/assets/Movies/Dune Part Two (2024)/poster.jpg")
    survivor_id = survivor.id
    scan = await find_mergeable(session)

    await merge(session, scan.plans)
    await session.commit()

    session.expire_all()
    poster = (await session.execute(select(Render))).scalar_one()
    assert poster.item_id == survivor_id
    assert poster.asset_path == "/assets/Movies/Dune Part Two (2024)/poster.jpg"


async def test_a_duplicate_render_is_deleted_and_the_survivors_is_kept(session):
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    kept = await _render(session, survivor.id, "poster", scored=True)
    kept_id = kept.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.renders_dropped == 1 and outcome.renders_repointed == 0
    session.expire_all()
    assert [r.id for r in (await session.execute(select(Render))).scalars()] == [kept_id]


async def test_children_are_repointed_before_the_delete_and_no_cascade_loss(session):
    """The most dangerous step in the module. ``media_items.parent_id`` is a
    self-FK with ON DELETE CASCADE, so a delete that ran first would take
    every season and episode under the stale show with it -- silently, with no
    audit row and nothing in the counts."""
    common = dict(kind="show", library="TV Shows", title="A Show",
                  tmdb_id=None, tvdb_id=77)
    stale = await _item(session, "100", **common)
    survivor = await _item(session, "200", **common)
    season = await _item(session, "110", kind="season", library="TV Shows",
                         title="A Show", tmdb_id=None, tvdb_id=77,
                         season_number=1, parent_id=stale.id)
    episode = await _item(session, "111", kind="episode", library="TV Shows",
                          title="An Episode", tmdb_id=None, tvdb_id=77,
                          season_number=1, episode_number=3, parent_id=season.id)
    survivor_id, season_id, episode_id = survivor.id, season.id, episode.id
    scan = await find_mergeable(session)
    assert [p.pair.stale.rating_key for p in scan.plans] == ["100"]

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.children_repointed == 1
    session.expire_all()
    rows = {row.id: row for row in (await session.execute(select(MediaItem))).scalars()}
    assert set(rows) == {survivor_id, season_id, episode_id}, (
        "the cascade took a live child with the stale row"
    )
    assert rows[season_id].parent_id == survivor_id
    assert rows[episode_id].parent_id == season_id


async def test_a_dismissal_the_survivor_lacks_is_repointed(session):
    """A4: it is repointed and it usually re-surfaces, because the evidence
    hash covers whether the row is scored. That is the dismissal contract --
    a dismissal holds only while the facts hold."""
    stale, survivor = await _pair(session)
    session.add(ActionDismissal(item_id=stale.id, art_kind="poster",
                                flag="language_miss", evidence="a" * 64))
    await session.commit()
    survivor_id = survivor.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.dismissals_repointed == 1 and outcome.dismissals_dropped == 0
    session.expire_all()
    assert (
        await session.execute(select(ActionDismissal))
    ).scalar_one().item_id == survivor_id


async def test_a_dismissal_both_rows_hold_is_dropped(session):
    stale, survivor = await _pair(session)
    for item in (stale, survivor):
        session.add(ActionDismissal(item_id=item.id, art_kind="poster",
                                    flag="language_miss", evidence="b" * 64))
    await session.commit()
    survivor_id = survivor.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.dismissals_dropped == 1
    session.expire_all()
    assert (
        await session.execute(select(ActionDismissal))
    ).scalar_one().item_id == survivor_id


async def test_facts_credits_and_the_logo_marker_are_carried(session):
    """``logo_upload_key`` is the marker that makes a logo revert safe. Losing
    it means the revert can never run again for that item -- which is why
    ``prune.retire`` writes it into its audit rather than letting it vanish."""
    stale, survivor = await _pair(session)
    stale.logo_upload_key = "upload://abc"
    session.add(ItemFacts(item_id=stale.id, critic_rating=7.5))
    session.add(ItemCredit(item_id=stale.id, kind="actor", person="Someone"))
    session.add(ItemCredit(item_id=survivor.id, kind="actor", person="Someone"))
    session.add(ItemCredit(item_id=stale.id, kind="director", person="Another"))
    await session.commit()
    survivor_id = survivor.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.facts_repointed == 1 and outcome.logos_carried == 1
    session.expire_all()
    survivor_row = (
        await session.execute(select(MediaItem).where(MediaItem.id == survivor_id))
    ).scalar_one()
    assert survivor_row.logo_upload_key == "upload://abc"
    facts = (await session.execute(select(ItemFacts))).scalar_one()
    assert facts.item_id == survivor_id and facts.critic_rating == pytest.approx(7.5)
    credits = {
        (credit.kind, credit.person)
        for credit in (await session.execute(select(ItemCredit))).scalars()
    }
    assert credits == {("actor", "Someone"), ("director", "Another")}


async def test_the_survivors_own_parent_link_is_carried_when_it_has_none(session):
    """A season minted by the fork resolved its parent by the LIVE rating key
    while the show's row still held the STALE one, so the parent lookup
    missed and the survivor was inserted with ``parent_id = NULL``. The stale
    season holds the correct link, and losing it orphans the survivor from
    its show."""
    show = await _item(session, "500", kind="show", library="TV Shows",
                        title="A Show", tmdb_id=None, tvdb_id=99)
    stale = await _item(session, "501", kind="season", library="TV Shows",
                         title="A Show", tmdb_id=None, tvdb_id=88,
                         season_number=1, parent_id=show.id)
    survivor = await _item(session, "502", kind="season", library="TV Shows",
                            title="A Show", tmdb_id=None, tvdb_id=88,
                            season_number=1, parent_id=None)
    show_id, survivor_id = show.id, survivor.id
    scan = await find_mergeable(session)

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.parents_carried == 1
    session.expire_all()
    survivor_row = (
        await session.execute(select(MediaItem).where(MediaItem.id == survivor_id))
    ).scalar_one()
    assert survivor_row.parent_id == show_id


async def test_the_merge_writes_one_audit_row_carrying_both_identities(session):
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    scan = await find_mergeable(session)

    await merge(session, scan.plans)
    await session.commit()

    session.expire_all()
    (audit,) = (
        await session.execute(select(EventLog).where(EventLog.source == MERGE_SOURCE))
    ).scalars().all()
    assert audit.event_type == MERGE_EVENT
    assert audit.payload["stale_rating_key"] == "1"
    assert audit.payload["survivor_rating_key"] == "2"
    assert audit.payload["renders_repointed"] == ["poster"]


async def test_a_row_re_upserted_under_the_pass_is_skipped_and_nothing_is_lost(
    session,
):
    """The concurrency guard, exercised through the actual writer. The delete
    is keyed on ``(id, updated_at)`` like ``prune.retire``'s, and the check
    runs under the row lock BEFORE anything is repointed -- so a skipped pair
    leaves the graph exactly as it found it, not half-moved."""
    stale, survivor = await _pair(session)
    # Captured before any ``expire_all`` below: reading ``.id`` off an expired
    # instance is a lazy load, which under asyncio is a MissingGreenlet rather
    # than a query.
    stale_id, survivor_id = stale.id, survivor.id
    await _render(session, stale.id, "poster")
    scan = await find_mergeable(session)

    resolved = ResolvedItem(
        rating_key="1", library="Movies", kind="movie", title="New Title",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)", file_path="/mnt/Media/Movies/x.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id=None,
    )
    await _upsert_media_item(session, resolved)
    await session.commit()

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.merged == [] and outcome.skipped == 1
    session.expire_all()
    assert {row.id for row in (await session.execute(select(MediaItem))).scalars()} == {
        stale_id, survivor_id,
    }
    poster = (await session.execute(select(Render))).scalar_one()
    assert poster.item_id == stale_id, "a skipped pair must not be half-merged"
    assert (
        await session.execute(select(EventLog).where(EventLog.source == MERGE_SOURCE))
    ).scalars().all() == []


async def test_a_survivor_re_upserted_under_the_pass_is_skipped_and_nothing_is_lost(
    session,
):
    """The guard is symmetric. A worker landing on the SURVIVOR's live key
    during ``verify_survivors``'s probe walk re-upserts ``media_items`` before
    it can go on to write a render -- if this pass did not notice, the
    repoint below would collide with that render (``uq_render_item_kind``) as
    an uncaught IntegrityError, not a clean skip."""
    stale, survivor = await _pair(session)
    stale_id, survivor_id = stale.id, survivor.id
    await _render(session, stale.id, "poster")
    scan = await find_mergeable(session)

    resolved = ResolvedItem(
        rating_key="2", library="Movies", kind="movie", title="Dune: Part Two",
        year=2024, season_number=None, episode_number=None,
        root_folder="Dune Part Two (2024)", file_path="/mnt/Media/Movies/x.mkv",
        art_url=None, tmdb_id=693134, tvdb_id=None, imdb_id=None,
    )
    await _upsert_media_item(session, resolved)
    await session.commit()

    outcome = await merge(session, scan.plans)
    await session.commit()

    assert outcome.merged == [] and outcome.skipped == 1
    session.expire_all()
    assert {row.id for row in (await session.execute(select(MediaItem))).scalars()} == {
        stale_id, survivor_id,
    }
    poster = (await session.execute(select(Render))).scalar_one()
    assert poster.item_id == stale_id, "a skipped pair must not be half-merged"


# --- the caps and the job --------------------------------------------------


def test_an_implausible_absolute_count_refuses_with_both_numbers():
    config = SimpleNamespace(max_merges=3, max_merge_share=0.25)

    refusal = implausible_merge_count(4, 100, config)

    assert refusal is not None
    assert "4" in refusal and "3" in refusal and "100" in refusal
    assert "refus" in refusal.lower()
    assert implausible_merge_count(3, 100, config) is None


def test_an_implausible_share_refuses_on_a_library_too_small_for_the_cap():
    config = SimpleNamespace(max_merges=500, max_merge_share=0.25)

    assert implausible_merge_count(10, 20, config) is not None
    # Below the minimum sample the share means nothing; only the absolute cap
    # applies -- "2 of 3 rows are twins" is a small library, not evidence.
    assert implausible_merge_count(2, 3, config) is None


async def test_the_job_is_named_and_paced_off_the_holder():
    holder = ConfigHolder(_config())
    job = make_merge_job(holder, lambda: FakePlex(), lambda: True)

    assert job.name == "plex_merge"
    assert job.current_interval() == 7 * 24 * 3600

    faster = _config()
    faster.scheduler.merge_days = 1
    holder.swap(faster)

    assert job.current_interval() == 24 * 3600


async def test_an_empty_media_items_table_refuses(session):
    def exploding_factory():
        raise AssertionError("no Plex client may be built for an empty table")

    job = make_merge_job(ConfigHolder(_config(apply=True)), exploding_factory,
                         lambda: True)

    summary = await job.run(session)

    assert "refus" in summary.lower() and "empty" in summary.lower()


async def test_the_dry_run_needs_no_plex_and_writes_nothing(session):
    """A5's whole point: the report is readable during an outage, so the
    operator can size the population before deciding anything."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    stale_id = stale.id

    def exploding_factory():
        raise AssertionError("the dry run must not build a Plex client")

    job = make_merge_job(ConfigHolder(_config()), exploding_factory, lambda: False)
    summary = await job.run(session)

    assert summary.startswith("dry run:")
    assert "1 render(s) would repoint" in summary
    session.expire_all()
    assert (
        await session.execute(select(MediaItem).where(MediaItem.id == stale_id))
    ).scalar_one_or_none() is not None
    assert (
        await session.execute(select(EventLog).where(EventLog.source == MERGE_SOURCE))
    ).scalars().all() == []


async def test_an_over_cap_scan_refuses_and_deletes_nothing(session):
    for index in range(6):
        await _item(session, str(100 + index), tmdb_id=1000 + index // 2)

    job = _job(_config(apply=True, max_merges=2), FakePlex(live={"101"}))
    summary = await job.run(session)

    assert "refus" in summary.lower()
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 6


async def test_an_unhealthy_plex_refuses_the_applied_pass(session):
    """The dry run runs anywhere; the APPLY must not, because the survivor
    cannot be verified and every merge ends in a delete."""
    await _pair(session)

    def exploding_factory():
        raise AssertionError("no Plex client may be built when Plex is unhealthy")

    job = make_merge_job(ConfigHolder(_config(apply=True)), exploding_factory,
                         lambda: False)
    summary = await job.run(session)

    assert "refus" in summary.lower() and "unhealthy" in summary.lower()
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 2


async def test_a_probe_failure_raises_and_names_only_the_exception_class(session):
    """``last_detail`` is rendered on the dashboard and a Plex error's message
    carries the server address and sometimes the token."""
    await _pair(session)
    plex = FakePlex(error=ConnectionError("https://plex.example:32400 reset"))

    job = _job(_config(apply=True), plex)

    with pytest.raises(MergeRefused) as caught:
        await job.run(session)

    message = str(caught.value)
    assert "ConnectionError" in message
    assert "plex.example" not in message and "reset" not in message
    session.expire_all()
    assert len((await session.execute(select(MediaItem))).scalars().all()) == 2


async def test_a_pair_whose_rows_both_resolve_by_key_is_refused(session):
    """Two real Plex items carrying one identity is a duplicate in the
    library: an operator's decision, not this job's."""
    await _pair(session)
    scan = await find_mergeable(session)

    probe = await verify_survivors(FakePlex(live={"1", "2"}), scan.plans)

    assert probe.accepted == [] and probe.both_live == 1


async def test_a_pair_whose_rows_resolve_by_neither_key_is_refused(session):
    """That is ``plex_prune``'s population, and taking it here would delete a
    row on the strength of a probe that says nothing."""
    await _pair(session)
    scan = await find_mergeable(session)

    probe = await verify_survivors(FakePlex(live=set()), scan.plans)

    assert probe.accepted == [] and probe.neither_live == 1


async def test_a_pair_where_the_older_key_is_the_live_one_is_refused(session):
    """The newest-key heuristic and the probe disagree, so the dry run's
    preview was wrong about this pair and nothing is deleted on it."""
    await _pair(session, stale_key="1", survivor_key="2")
    scan = await find_mergeable(session)

    probe = await verify_survivors(FakePlex(live={"1"}), scan.plans)

    assert probe.accepted == [] and probe.election_disagreed == 1


async def test_an_applied_pass_merges_and_dismisses_jobs_for_both_keys(session):
    """A6: an in-flight or parked payload can name either key -- the dead one
    because it was queued before the fork, the survivor's because the graph it
    was queued against has just changed."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    survivor_id = survivor.id
    for key in ("1", "2"):
        session.add(Job(
            kind="process_item",
            payload={"kind": "movie", "title": "Dune", "rating_key": key},
            dedupe_key=f"process_item:movie:tmdb693134:{key}",
            state="parked",
        ))
    await session.commit()

    job = _job(_config(apply=True), FakePlex(live={"2"}))
    summary = await job.run(session)

    assert summary.startswith("merged 1 of 2")
    assert "dismissed 2 queued job(s)" in summary
    session.expire_all()
    assert [row.id for row in (await session.execute(select(MediaItem))).scalars()] == [
        survivor_id
    ]
    assert {j.state for j in (await session.execute(select(Job))).scalars()} == {
        "dismissed"
    }


async def test_the_applied_summary_reports_repoints_and_drops_separately(session):
    """Deleted duplicates shrink the Action Center's denominator; repointed
    orphans stay in it and become finishable. Reporting one number would let
    an operator read the first as data loss."""
    stale, survivor = await _pair(session)
    await _render(session, stale.id, "poster")
    await _render(session, stale.id, "background")
    await _render(session, survivor.id, "poster", scored=True)

    job = _job(_config(apply=True), FakePlex(live={"2"}))
    summary = await job.run(session)

    assert "repointed 1 render(s)" in summary
    assert "dropped 1" in summary


async def test_intent_for_row_carries_the_rows_stored_key_and_ids(session):
    """The probe must ask exactly what the pipeline asks, or the job would
    decide a row's fate on a question the pipeline never poses."""
    stale, _ = await _pair(session)
    scan = await find_mergeable(session)

    intent = intent_for_row(scan.plans[0].pair.stale)

    assert intent.rating_key == stale.rating_key
    assert intent.kind == "movie"
    assert intent.tmdb_id == 693134
    assert intent.title == "Dune: Part Two"
```

- [ ] **Step 9: Run them and watch them fail**

```bash
docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk2-red2 test sh -c \
  "set -o pipefail; pytest tests/test_scheduler_merge_job.py -q 2>&1 | tee /app/.superpowers/run-pirk2-red2.log"
```

Expected: a collection error — `ModuleNotFoundError: No module named 'autoposter.scheduler.merge'`. That is the right first failure; the module does not exist yet. Then `docker rm pirk2-red2`.

- [ ] **Step 10: Write `src/autoposter/scheduler/merge.py`**

```python
"""Merging the twin ``media_items`` rows a re-key was too late to prevent.

An item re-matched or renumbered in Plex resolves to a key its stored row does
not hold. ``_upsert_media_item`` (``render/pipeline.py``) is an ``ON CONFLICT
(rating_key)`` upsert, so before that module learned to re-key, a new key
conflicted with nothing and INSERTED: a second row that inherited every future
render, while the original kept its renders, its facts, its credits, its
dismissals, its ``logo_upload_key`` and its children and was never written
again.

``scheduler/prune.py`` cannot own this population and could not be made to
without changing what "gone" means: its probe asks whether the pipeline can
still resolve a row, and a re-keyed item resolves -- by the same GUID walk
that forked it. That is why the prune sweep correctly reported ``pruned 0``
against a library full of twins. Deleting the stale row IS the right end, but
it is a merge's last step and not the whole of it: the stale row can hold the
only ``item_facts``, the only credits, the only ``logo_upload_key`` and the
parent link for every season and episode under it.

A maintenance job rather than a migration, for four reasons in order of
weight. The population regenerates until every producing path carries the fix.
The survivor election needs a live Plex probe, and Alembic must never depend
on a reachable server. The operator's stated need is to see it first, which
``apply: false`` plus one summary string already is everywhere else in this
tree. And a migration cannot refuse and cannot be resumed -- a partial failure
mid-merge would leave a half-repointed graph with no audit trail.

**Nothing moves on disk.** ``render/naming.py``'s ``asset_path`` is keyed by
library and root folder, never by the rating key, so both rows of a pair
record byte-identical paths: repointing a render leaves the file where it is,
and deleting one leaves it there too. ``asset_cleanup`` needs no change.

**The cascade is the danger.** ``media_items.parent_id`` is a self-FK with ON
DELETE CASCADE, so deleting the stale row of a show pair would take every
season and episode under it. Children are repointed onto the survivor BEFORE
the delete, in the same transaction. Nothing in this module may be reordered
past that.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.artwork_modes.base import SHARE_CHECK_MIN_ITEMS, refuse_if_empty
from autoposter.config.holder import ConfigHolder
from autoposter.db.models import (
    ActionDismissal,
    EventLog,
    ItemCredit,
    ItemFacts,
    MediaItem,
    Render,
)
from autoposter.intake.arr import RenderIntent
from autoposter.scheduler.core import Job
# Reused verbatim rather than reimplemented: an in-flight payload names a
# rating key and nothing else, and two spellings of "find the jobs for this
# key" would eventually disagree about which states count.
from autoposter.scheduler.prune import dismiss_jobs_for

logger = logging.getLogger(__name__)

# The events_log identity of a merge. Constants for the reason
# PRUNE_SOURCE/PRUNE_EVENT are: the audit row is the only surviving record of
# a deleted row, and a typo in either would make them unfindable.
MERGE_SOURCE = "merge"
MERGE_EVENT = "media_items_merged"


class MergeRefused(Exception):
    """A pass that could not be trusted to run at all.

    The ``PruneRefused`` shape, and drawn on the same line: raised for what
    interrupts the pass mid-walk with its own state no longer known; returned
    as an ordinary summary for a pass that ran correctly and declined the work
    it found, and for the unhealthy-Plex refusal, which describes a pass that
    never started.
    """

    # Read by scheduler/core.py's failure branch: every message this is raised
    # with is hand-built served-safe -- the probe-failure site names the
    # exception class only, never str(exc) and never a URL -- so the scheduler
    # serves it verbatim instead of narrowing it to "MergeRefused".
    served_detail = True


@dataclass(frozen=True)
class MergeRow:
    """One ``media_items`` row, as plain data.

    Read as columns rather than as an ORM object for ``PruneCandidate``'s
    reason: the scan holds the whole library at once, and ``updated_at`` has
    to survive into the delete as a value the session cannot refresh
    underneath it -- it is half the delete's key.
    """

    id: int
    rating_key: str
    kind: str
    library: str
    title: str
    parent_id: int | None
    tmdb_id: int | None
    tvdb_id: int | None
    imdb_id: str | None
    year: int | None
    season_number: int | None
    episode_number: int | None
    logo_upload_key: str | None
    facts_attempted_at: datetime | None
    credits_attempted_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class MergePair:
    """One elected pair: the row that dies and the row that lives."""

    stale: MergeRow
    survivor: MergeRow


@dataclass(frozen=True)
class MergePlan:
    """Everything one merge would do, decided before anything is written.

    Computed once and used by BOTH the dry run and the applied pass, so the
    dry run and the apply never DESCRIBE a pair's merge differently -- the
    failure a separate "count it" query would eventually produce. It does NOT
    freeze the underlying rows: the window between the scan and ``merge``
    includes the whole of ``verify_survivors``' live Plex walk, and a worker
    can write either row of a pair during it. ``merge`` re-locks both rows and
    compares them against the values captured here before writing anything, so
    a pair that drifted is skipped whole rather than partially applied -- see
    the guard in ``merge``'s own docstring.
    """

    pair: MergePair
    renders_repoint: list[str]
    renders_drop: list[str]
    dismissals_repoint: list[str]
    dismissals_drop: list[str]
    children: int
    facts_repoint: bool
    logo_carried: bool


@dataclass(frozen=True)
class MergeScan:
    """What one probe-free scan found.

    ``no_identity_match`` and ``neither_scored`` are reported separately and
    never merged into the headline: the first is the population this job
    cannot reach (an adopted season or episode stores its OWN external ids
    while ``resolve()`` reports the show's, so those rows have no twin this
    predicate can see), and the second is a pair where neither row was ever
    scored -- a different defect, which this job must not claim credit for.
    """

    plans: list[MergePlan]
    ambiguous: int
    unelectable: int
    no_identity_match: int
    neither_scored: int
    total: int


@dataclass(frozen=True)
class ProbeResult:
    """The applied pass's live verification of each elected pair."""

    accepted: list[MergePlan]
    both_live: int
    neither_live: int
    election_disagreed: int


@dataclass(frozen=True)
class MergeOutcome:
    """What one applied pass actually did.

    ``merged`` carries ``(stale key, survivor key)`` per completed merge, not
    the pairs offered: the job disposal downstream keys off what happened, or
    it would dismiss the queued work of a pair that was skipped.
    """

    merged: list[tuple[str, str]]
    renders_repointed: int
    renders_dropped: int
    dismissals_repointed: int
    dismissals_dropped: int
    children_repointed: int
    parents_carried: int
    facts_repointed: int
    logos_carried: int
    skipped: int


def intent_for_row(row: MergeRow) -> RenderIntent:
    """The intent the pipeline would build for this row.

    Field for field what ``prune.intent_for`` builds, ``rating_key``
    included -- the probe has to ask exactly what the pipeline asks, or this
    job would decide a row's fate on a question the pipeline never poses.
    """
    return RenderIntent(
        kind=row.kind,
        title=row.title,
        tmdb_id=row.tmdb_id,
        tvdb_id=row.tvdb_id,
        imdb_id=row.imdb_id,
        year=row.year,
        season_number=row.season_number,
        episode_number=row.episode_number,
        rating_key=row.rating_key,
    )


def _identity_tokens(row: MergeRow) -> list[tuple[str, object]]:
    """The external ids this row can be recognised by. Empty means unmatchable
    -- a row with no external id is never paired on a title."""
    tokens: list[tuple[str, object]] = []
    if row.tmdb_id is not None:
        tokens.append(("tmdb", row.tmdb_id))
    if row.tvdb_id is not None:
        tokens.append(("tvdb", row.tvdb_id))
    if row.imdb_id is not None:
        tokens.append(("imdb", row.imdb_id))
    return tokens


def _identity_clusters(rows: list[MergeRow]) -> list[list[MergeRow]]:
    """Every set of two or more rows sharing one identity.

    Union-find rather than the operator's pairwise self-join, because a
    self-join answers about PAIRS and the decision here is about CLUSTERS:
    three rows for one item is not three independent merges, it is one
    ambiguity, and a pairwise walk would collapse them in an order nobody
    chose. The sizing query in ``deploy/README.md`` stays the independent
    cross-check, not the implementation.

    Rows arrive in ``id`` order and every group preserves it, so the pass is
    deterministic and reads no clock.
    """
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(one: int, other: int) -> None:
        root_one, root_other = find(one), find(other)
        if root_one != root_other:
            parent[max(root_one, root_other)] = min(root_one, root_other)

    seen: dict[tuple, int] = {}
    for index, row in enumerate(rows):
        # The scope IS part of the key: same kind, same library, same
        # season/episode coordinates. Without the coordinates every season of
        # one show shares the show's ids and the whole series reads as one
        # cluster; without the library, a 4K copy and an HD copy of one film
        # read as twins and merging them is data loss.
        scope = (row.kind, row.library, row.season_number, row.episode_number)
        for token in _identity_tokens(row):
            key = (scope, token)
            other = seen.get(key)
            if other is None:
                seen[key] = index
            else:
                union(other, index)

    grouped: dict[int, list[MergeRow]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(find(index), []).append(row)
    return [members for members in grouped.values() if len(members) > 1]


async def _all_rows(session: AsyncSession) -> list[MergeRow]:
    rows = (
        await session.execute(
            select(
                MediaItem.id,
                MediaItem.rating_key,
                MediaItem.kind,
                MediaItem.library,
                MediaItem.title,
                MediaItem.parent_id,
                MediaItem.tmdb_id,
                MediaItem.tvdb_id,
                MediaItem.imdb_id,
                MediaItem.year,
                MediaItem.season_number,
                MediaItem.episode_number,
                MediaItem.logo_upload_key,
                MediaItem.facts_attempted_at,
                MediaItem.credits_attempted_at,
                MediaItem.updated_at,
            ).order_by(MediaItem.id)
        )
    ).all()
    return [MergeRow(**row._mapping) for row in rows]


async def _plan_merges(
    session: AsyncSession, pairs: list[MergePair]
) -> list[MergePlan]:
    """Decide every pair's work in four queries, not four per pair."""
    if not pairs:
        return []
    ids = [row.id for pair in pairs for row in (pair.stale, pair.survivor)]
    stale_ids = [pair.stale.id for pair in pairs]

    render_kinds: dict[int, set[str]] = {}
    for item_id, art_kind in (
        await session.execute(
            select(Render.item_id, Render.art_kind).where(Render.item_id.in_(ids))
        )
    ).all():
        render_kinds.setdefault(item_id, set()).add(art_kind)

    dismissal_kinds: dict[int, set[str]] = {}
    for item_id, art_kind in (
        await session.execute(
            select(ActionDismissal.item_id, ActionDismissal.art_kind)
            .where(ActionDismissal.item_id.in_(ids))
        )
    ).all():
        dismissal_kinds.setdefault(item_id, set()).add(art_kind)

    facts_ids = set(
        (
            await session.execute(
                select(ItemFacts.item_id).where(ItemFacts.item_id.in_(ids))
            )
        ).scalars()
    )

    children = dict(
        (
            await session.execute(
                select(MediaItem.parent_id, func.count())
                .where(MediaItem.parent_id.in_(stale_ids))
                .group_by(MediaItem.parent_id)
            )
        ).all()
    )

    plans = []
    for pair in pairs:
        stale_renders = render_kinds.get(pair.stale.id, set())
        survivor_renders = render_kinds.get(pair.survivor.id, set())
        stale_dismissals = dismissal_kinds.get(pair.stale.id, set())
        survivor_dismissals = dismissal_kinds.get(pair.survivor.id, set())
        plans.append(MergePlan(
            pair=pair,
            # Sorted so the plan, the summary and the audit row all name the
            # kinds in one order -- a set's iteration order is not one.
            renders_repoint=sorted(stale_renders - survivor_renders),
            renders_drop=sorted(stale_renders & survivor_renders),
            dismissals_repoint=sorted(stale_dismissals - survivor_dismissals),
            dismissals_drop=sorted(stale_dismissals & survivor_dismissals),
            children=children.get(pair.stale.id, 0),
            facts_repoint=(
                pair.stale.id in facts_ids and pair.survivor.id not in facts_ids
            ),
            logo_carried=(
                pair.stale.logo_upload_key is not None
                and pair.survivor.logo_upload_key is None
            ),
        ))
    return plans


async def find_mergeable(session: AsyncSession) -> MergeScan:
    """Every twin pair, elected and planned, with no Plex probe at all.

    Probe-free on purpose (A5): the operator can read this report during an
    outage, size the population, and only then decide. The election is the
    newest rating key -- Plex numbers upward, so the row created by the fork
    is the live one in every case the recon examined -- and the APPLIED pass
    verifies it against Plex before it deletes anything, which is where the
    heuristic earns its keep or is refused.
    """
    rows = await _all_rows(session)
    if not rows:
        return MergeScan([], 0, 0, 0, 0, 0)

    pairs: list[MergePair] = []
    ambiguous = 0
    unelectable = 0
    clustered_ids: set[int] = set()
    for cluster in _identity_clusters(rows):
        clustered_ids.update(row.id for row in cluster)
        if len(cluster) != 2:
            ambiguous += 1
            continue
        if not all(row.rating_key.isdigit() for row in cluster):
            # The election orders keys as integers. A key that is not a number
            # cannot be ordered, and guessing an order would guess which row
            # dies.
            unelectable += 1
            continue
        survivor, stale = sorted(
            cluster, key=lambda row: int(row.rating_key), reverse=True
        )
        pairs.append(MergePair(stale=stale, survivor=survivor))

    plans = await _plan_merges(session, pairs)

    unscored = select(Render.item_id).where(
        Render.status == "rendered", Render.quality_scored_at.is_(None)
    )
    if clustered_ids:
        unscored = unscored.where(Render.item_id.notin_(clustered_ids))
    no_identity_match = len(set((await session.execute(unscored)).scalars()))

    paired_ids = [row.id for pair in pairs for row in (pair.stale, pair.survivor)]
    scored_ids: set[int] = set()
    if paired_ids:
        scored_ids = set(
            (
                await session.execute(
                    select(Render.item_id)
                    .where(Render.item_id.in_(paired_ids))
                    .where(Render.quality_scored_at.isnot(None))
                )
            ).scalars()
        )
    neither_scored = sum(
        1
        for pair in pairs
        if pair.stale.id not in scored_ids and pair.survivor.id not in scored_ids
    )

    return MergeScan(
        plans=plans,
        ambiguous=ambiguous,
        unelectable=unelectable,
        no_identity_match=no_identity_match,
        neither_scored=neither_scored,
        total=len(rows),
    )


async def verify_survivors(plex, plans: list[MergePlan]) -> ProbeResult:
    """Keep only the pairs Plex agrees about, in one walk.

    ``keys_resolve`` and never ``exists_many``: the latter falls through to
    the GUID walk, under which a re-keyed item resolves from BOTH rows and the
    probe would say nothing at all. Three refusals, counted apart because they
    mean three different things to an operator:

    * **both keys live** -- two real Plex items carrying one identity, i.e. a
      duplicate in the library. An operator's decision, not this job's.
    * **neither key live** -- ``plex_prune``'s population. Deleting here would
      act on a probe that says nothing about which row is real.
    * **the election disagreed** -- the newest key is the one Plex refuses, so
      the dry run's preview was wrong about this pair and nothing is done to
      it. It is reported so the heuristic can be judged on evidence.
    """
    if not plans:
        return ProbeResult([], 0, 0, 0)
    intents = [intent_for_row(plan.pair.survivor) for plan in plans]
    intents += [intent_for_row(plan.pair.stale) for plan in plans]
    flags = await plex.keys_resolve(intents)
    survivor_flags = flags[: len(plans)]
    stale_flags = flags[len(plans):]

    accepted: list[MergePlan] = []
    both = neither = disagreed = 0
    for plan, survivor_live, stale_live in zip(
        plans, survivor_flags, stale_flags, strict=True
    ):
        if survivor_live and stale_live:
            both += 1
        elif not survivor_live and not stale_live:
            neither += 1
        elif stale_live:
            disagreed += 1
        else:
            accepted.append(plan)
    return ProbeResult(
        accepted=accepted,
        both_live=both,
        neither_live=neither,
        election_disagreed=disagreed,
    )


def _later(one: datetime | None, other: datetime | None) -> datetime | None:
    """The later of two "we looked" stamps, ignoring NULLs.

    Taking the survivor's alone would make it read as unvisited whenever the
    stale row was the one that had been swept, and it would be re-swept for
    nothing.
    """
    if one is None:
        return other
    if other is None:
        return one
    return max(one, other)


async def merge(session: AsyncSession, plans: list[MergePlan]) -> MergeOutcome:
    """Apply each plan, one committed transaction per pair.

    **One transaction per pair is what makes this resumable.** A failure part
    way through leaves every completed merge committed and audited and every
    remaining pair untouched, so the next pass simply continues. That is the
    thing a migration could not offer.

    **Both rows are locked, and the change test runs under that lock BEFORE
    anything is written.** ``prune.retire`` can put its ``(id, updated_at)``
    guard on the delete itself, because the delete is the only write it makes.
    Here the delete is the LAST of seven writes, and a guard that fired at the
    end would leave a half-moved graph. So the pair is taken ``FOR UPDATE``,
    and BOTH rows' ``updated_at`` -- not just the stale row's -- are compared
    to the values the scan read; a pair where EITHER row changed is skipped
    with no write at all. The survivor side is not redundant: the window this
    guard closes is the whole of ``verify_survivors``'s live Plex walk
    (minutes, not milliseconds, at the pass's own 500-pair cap), and a worker
    that lands on the survivor's live key during it re-upserts ``media_items``
    -- which always runs before that same worker can go on to write a
    ``renders`` row -- so checking the survivor's stamp here catches the
    upsert before it happens, rather than letting a later repoint collide with
    a render that worker just inserted (``uq_render_item_kind``) as an
    uncaught ``IntegrityError`` past ``dismiss_jobs_for``. Under the lock a
    concurrent ``_upsert_media_item`` waits, then finds the row gone and
    inserts a fresh one -- a new twin, which the next pass merges. The guard
    stays on the delete as well, because a guard that can only be redundant is
    cheaper than a guard that can only be missing.

    **The locked read selects COLUMNS, not ORM rows, and that is not a style
    choice.** ``_upsert_media_item``'s ``ON CONFLICT`` arm stamps
    ``updated_at`` with a server-side ``now()``, which SQLAlchemy does not
    write back into an instance already in the identity map; a
    ``select(MediaItem)`` would hand back that instance with its stale
    in-memory timestamp and the guard would compare the scan's value against
    itself and always match. A column select reads the real row. It is the
    same reason ``prune.retire`` compares ``updated_at`` inside the DELETE's
    own WHERE clause rather than in Python.

    **The survivor's OWN parent link is carried too, decided off the same
    locked read.** A season or episode minted by the fork resolved its parent
    by the LIVE rating key while the show's row still held the STALE one, so
    ``_upsert_media_item``'s parent lookup missed and the survivor was
    inserted with ``parent_id = NULL``. The stale row holds the correct link;
    losing it orphans the survivor from its show until a later pass happens to
    re-resolve the parent's own key. Carried onto the survivor exactly like
    ``logo_upload_key``, and only when the survivor's is NULL -- a parent it
    has acquired since the scan must not be overwritten.

    **Children are repointed BEFORE the delete.** ``media_items.parent_id`` is
    a self-FK with ON DELETE CASCADE: a delete that ran first would take every
    season and episode under a stale show, silently, with no audit row and
    absent from every count. Nothing in this function may be reordered past
    that line.

    A repointed dismissal will usually re-surface on the queue, because
    ``action_dismissals.evidence`` hashes the render facts the queue judges
    and the survivor is scored where the stale row was not. That is the
    dismissal contract -- it holds only while the facts hold -- and the
    summary says so rather than hiding it.
    """
    merged: list[tuple[str, str]] = []
    renders_repointed = renders_dropped = 0
    dismissals_repointed = dismissals_dropped = 0
    children_repointed = parents_carried = facts_repointed = logos_carried = 0
    skipped = 0

    for plan in plans:
        pair = plan.pair
        locked = {
            row.id: row
            for row in (
                await session.execute(
                    select(
                        MediaItem.id,
                        MediaItem.updated_at,
                        MediaItem.parent_id,
                        MediaItem.logo_upload_key,
                        MediaItem.facts_attempted_at,
                        MediaItem.credits_attempted_at,
                    )
                    .where(MediaItem.id.in_((pair.stale.id, pair.survivor.id)))
                    .order_by(MediaItem.id)
                    .with_for_update()
                )
            ).all()
        }
        stale = locked.get(pair.stale.id)
        survivor = locked.get(pair.survivor.id)
        if (
            stale is None
            or survivor is None
            or stale.updated_at != pair.stale.updated_at
            or survivor.updated_at != pair.survivor.updated_at
        ):
            logger.info(
                "merge: %s -> %s changed under the pass; left alone",
                pair.stale.rating_key, pair.survivor.rating_key,
            )
            await session.rollback()
            skipped += 1
            continue

        if plan.renders_drop:
            await session.execute(
                delete(Render)
                .where(Render.item_id == stale.id)
                .where(Render.art_kind.in_(plan.renders_drop))
                .execution_options(synchronize_session=False)
            )
        if plan.renders_repoint:
            await session.execute(
                update(Render)
                .where(Render.item_id == stale.id)
                .where(Render.art_kind.in_(plan.renders_repoint))
                .values(item_id=survivor.id)
                .execution_options(synchronize_session=False)
            )
        if plan.dismissals_drop:
            await session.execute(
                delete(ActionDismissal)
                .where(ActionDismissal.item_id == stale.id)
                .where(ActionDismissal.art_kind.in_(plan.dismissals_drop))
                .execution_options(synchronize_session=False)
            )
        if plan.dismissals_repoint:
            await session.execute(
                update(ActionDismissal)
                .where(ActionDismissal.item_id == stale.id)
                .where(ActionDismissal.art_kind.in_(plan.dismissals_repoint))
                .values(item_id=survivor.id)
                .execution_options(synchronize_session=False)
            )
        if plan.facts_repoint:
            await session.execute(
                update(ItemFacts)
                .where(ItemFacts.item_id == stale.id)
                .values(item_id=survivor.id)
                .execution_options(synchronize_session=False)
            )
        else:
            # UNIQUE(item_id) forbids two rows on the survivor, and the
            # survivor's is the fresher of the two by construction -- it is
            # the row every sweep since the fork has been writing.
            await session.execute(
                delete(ItemFacts)
                .where(ItemFacts.item_id == stale.id)
                .execution_options(synchronize_session=False)
            )
        # Credits are a composite-PK many-to-many, so the collision is per
        # (kind, person) rather than per row: drop the ones the survivor
        # already holds, then repoint the rest.
        await session.execute(
            delete(ItemCredit)
            .where(ItemCredit.item_id == stale.id)
            .where(
                tuple_(ItemCredit.kind, ItemCredit.person).in_(
                    select(ItemCredit.kind, ItemCredit.person)
                    .where(ItemCredit.item_id == survivor.id)
                )
            )
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            update(ItemCredit)
            .where(ItemCredit.item_id == stale.id)
            .values(item_id=survivor.id)
            .execution_options(synchronize_session=False)
        )

        # Decided off the LOCKED values, not off the plan's: the plan is the
        # dry run's preview, read before the lock, and a logo marker the
        # survivor has acquired since then must not be overwritten. The plan's
        # own flag stays what the report was built from.
        carry_logo = (
            stale.logo_upload_key is not None and survivor.logo_upload_key is None
        )
        carried_logo = stale.logo_upload_key if carry_logo else None
        # Same reasoning, for the survivor's OWN parent link. A season or
        # episode minted by the fork resolved its parent by the LIVE rating
        # key while the show's row still held the STALE one
        # (``render/pipeline.py``'s ``_upsert_media_item`` looks
        # ``parent_rating_key`` up against the CURRENT ``rating_key``), so the
        # lookup missed and the survivor was inserted with ``parent_id =
        # NULL``. The stale row holds the correct link; without this it dies
        # with the row and the survivor is orphaned from its show until a
        # later pass happens to re-resolve the parent's own key.
        carry_parent = stale.parent_id is not None and survivor.parent_id is None
        carried_parent = stale.parent_id if carry_parent else None
        survivor_values = {
            "facts_attempted_at": _later(
                survivor.facts_attempted_at, stale.facts_attempted_at
            ),
            "credits_attempted_at": _later(
                survivor.credits_attempted_at, stale.credits_attempted_at
            ),
        }
        if carry_logo:
            survivor_values["logo_upload_key"] = carried_logo
        if carry_parent:
            survivor_values["parent_id"] = carried_parent
        await session.execute(
            update(MediaItem)
            .where(MediaItem.id == survivor.id)
            .values(**survivor_values)
            .execution_options(synchronize_session=False)
        )

        # THE CASCADE LAW. This runs before the delete below, always.
        if plan.children:
            await session.execute(
                update(MediaItem)
                .where(MediaItem.parent_id == stale.id)
                .values(parent_id=survivor.id)
                .execution_options(synchronize_session=False)
            )

        removed = (
            await session.execute(
                delete(MediaItem)
                .where(MediaItem.id == stale.id)
                .where(MediaItem.updated_at == pair.stale.updated_at)
                .returning(MediaItem.id)
                .execution_options(synchronize_session=False)
            )
        ).first()
        if removed is None:
            # Unreachable while the lock above holds, and kept anyway: this is
            # the one write whose failure would be irreversible in the other
            # direction, and a redundant guard costs a comparison.
            logger.warning(
                "merge: %s changed between the lock and the delete; rolled back",
                pair.stale.rating_key,
            )
            await session.rollback()
            skipped += 1
            continue

        session.add(EventLog(
            source=MERGE_SOURCE,
            event_type=MERGE_EVENT,
            payload={
                "stale_media_item_id": pair.stale.id,
                "stale_rating_key": pair.stale.rating_key,
                "survivor_media_item_id": pair.survivor.id,
                "survivor_rating_key": pair.survivor.rating_key,
                "kind": pair.stale.kind,
                "library": pair.stale.library,
                "title": pair.stale.title,
                "season_number": pair.stale.season_number,
                "episode_number": pair.stale.episode_number,
                "tmdb_id": pair.stale.tmdb_id,
                "tvdb_id": pair.stale.tvdb_id,
                "imdb_id": pair.stale.imdb_id,
                "renders_repointed": plan.renders_repoint,
                "renders_dropped": plan.renders_drop,
                "dismissals_repointed": plan.dismissals_repoint,
                "dismissals_dropped": plan.dismissals_drop,
                "children_repointed": plan.children,
                "parent_id_carried": carried_parent,
                "facts_repointed": plan.facts_repoint,
                "logo_upload_key_carried": carried_logo,
                # Recorded unconditionally, prune.retire's precedent: when
                # BOTH rows already hold a marker, carry_logo is False and the
                # stale one dies with the row -- an operator must still be
                # able to find the value that was lost, not just learn that
                # something was.
                "stale_logo_upload_key": stale.logo_upload_key,
            },
            outcome=(
                f"merged {pair.stale.rating_key} into {pair.survivor.rating_key} "
                "on an identity match"
            ),
        ))
        await session.commit()

        merged.append((pair.stale.rating_key, pair.survivor.rating_key))
        renders_repointed += len(plan.renders_repoint)
        renders_dropped += len(plan.renders_drop)
        dismissals_repointed += len(plan.dismissals_repoint)
        dismissals_dropped += len(plan.dismissals_drop)
        children_repointed += plan.children
        parents_carried += int(carry_parent)
        facts_repointed += int(plan.facts_repoint)
        logos_carried += int(carry_logo)

    return MergeOutcome(
        merged=merged,
        renders_repointed=renders_repointed,
        renders_dropped=renders_dropped,
        dismissals_repointed=dismissals_repointed,
        dismissals_dropped=dismissals_dropped,
        children_repointed=children_repointed,
        parents_carried=parents_carried,
        facts_repointed=facts_repointed,
        logos_carried=logos_carried,
        skipped=skipped,
    )


def implausible_merge_count(pairs: int, total: int, config) -> str | None:
    """Return a refusal summary if this many twin pairs cannot be believed.

    ``implausible_prune_count``'s shape with this sweep's causes. The failure
    it catches is not a server that answered wrongly -- this scan asks no
    server -- but an identity predicate that has become too generous: two
    libraries renamed into one name, an import that duplicated external ids, a
    restore that doubled the table. Every merge ends in a delete, so past
    either cap the pass reports the numbers instead of acting on them.
    """
    if pairs and pairs > config.max_merges:
        return (
            f"refused: {pairs} of {total} media_items row(s) look like twin "
            f"pairs, more than the safety cap of {config.max_merges}; this "
            "usually means two libraries now share a name, an import "
            "duplicated external ids, or a restore doubled the table -- "
            "change nothing"
        )
    share = pairs / total if total else 0.0
    if total >= SHARE_CHECK_MIN_ITEMS and share > config.max_merge_share:
        return (
            f"refused: {pairs} of {total} media_items row(s) look like twin "
            f"pairs ({share:.0%}), more than the safety cap of "
            f"{config.max_merge_share:.0%}; this usually means two libraries "
            "now share a name, an import duplicated external ids, or a "
            "restore doubled the table -- change nothing"
        )
    return None


def _tail(scan: MergeScan) -> str:
    """The sentences both summaries end with: what this pass did NOT do."""
    parts = []
    if scan.no_identity_match:
        parts.append(
            f"{scan.no_identity_match} unscored row(s) have no identity twin "
            "at all and are untouched by this job"
        )
    if scan.neither_scored:
        parts.append(
            f"{scan.neither_scored} pair(s) have no scored render on either "
            "row, which is a different problem"
        )
    if scan.ambiguous or scan.unelectable:
        parts.append(
            f"{scan.ambiguous} cluster(s) ambiguous and {scan.unelectable} "
            "unelectable, left alone -- neither resolves itself; an operator "
            "must fix the identity in Plex or dismiss the row, or this job "
            "reports them again next pass"
        )
    return ("; " + "; ".join(parts)) if parts else ""


def make_merge_job(
    holder: ConfigHolder,
    plex_factory: Callable[[], object],
    is_healthy: Callable[[], bool],
) -> Job:
    """Build the scheduled twin-merge job.

    The ``make_prune_job`` shape: scheduled, dry run by default, one summary
    string per run, hand-triggerable once ``"plex_merge"`` is in
    ``SCHEDULED_JOB_NAMES``. Not a Plex-writing mode -- this writes only to the
    database, so the mode fence buys it nothing.

    One deliberate departure from the prune's ordering: the unhealthy-Plex
    refusal is checked **after** the scan and only on the applied path. The
    prune checks it first because a dead server makes every row read as gone;
    this scan asks no server at all, so refusing a read-only report during an
    outage would deny the operator the one report they can always have. The
    apply still refuses, because a survivor that cannot be verified must not
    have its twin deleted.
    """

    async def run(session: AsyncSession) -> str:
        config = holder.current

        empty = await refuse_if_empty(session, MediaItem, table_name="media_items")
        if empty is not None:
            return empty

        scan = await find_mergeable(session)
        refusal = implausible_merge_count(len(scan.plans), scan.total, config.merge)
        if refusal is not None:
            return refusal

        if not config.merge.apply:
            repoint = sum(len(plan.renders_repoint) for plan in scan.plans)
            drop = sum(len(plan.renders_drop) for plan in scan.plans)
            moved = sum(len(plan.dismissals_repoint) for plan in scan.plans)
            dropped = sum(len(plan.dismissals_drop) for plan in scan.plans)
            children = sum(plan.children for plan in scan.plans)
            return (
                f"dry run: {len(scan.plans)} of {scan.total} media_items row(s) "
                f"would be merged into their twin; {repoint} render(s) would "
                f"repoint and {drop} would be dropped; {moved} dismissal(s) "
                f"would move and {dropped} would be dropped; {children} child "
                f"row(s) would repoint before the delete"
                + _tail(scan)
            )

        if not is_healthy():
            return (
                "refused: Plex is unhealthy, so no surviving row can be "
                "verified before its twin is deleted; change nothing"
            )

        try:
            # The factory is inside the try because it connects: a refused
            # connection, a rejected token or a plexapi BadRequest all raise
            # here, and every one of those messages carries the server address.
            plex = await asyncio.to_thread(plex_factory)
            probe = await verify_survivors(plex, scan.plans)
        except Exception as exc:
            # The class name only, never str(exc) and never a URL: this string
            # is stored in scheduled_runs.last_detail, which the dashboard
            # renders. The full traceback goes to the log.
            logger.warning("plex_merge: verifying survivors failed", exc_info=True)
            raise MergeRefused(
                f"refused: verifying the surviving rows failed "
                f"({type(exc).__name__}), so no row's death can be trusted; "
                "change nothing"
            ) from None

        outcome = await merge(session, probe.accepted)
        # BOTH keys (A6): a parked payload can name the dead key, because it
        # was queued before the fork, or the survivor's, because the graph it
        # was queued against has just changed under it. Rewriting a claimed
        # job's payload is the one thing this queue never does, so disposal
        # looks up both instead.
        dismissed = await dismiss_jobs_for(
            session, [key for pair in outcome.merged for key in pair]
        )
        await session.commit()

        summary = (
            f"merged {len(outcome.merged)} of {scan.total} media_items row(s) "
            f"into their twin; repointed {outcome.renders_repointed} render(s) "
            f"and dropped {outcome.renders_dropped}; moved "
            f"{outcome.dismissals_repointed} dismissal(s) and dropped "
            f"{outcome.dismissals_dropped}; repointed "
            f"{outcome.children_repointed} child row(s) and "
            f"{outcome.parents_carried} survivor parent link(s); kept "
            f"{outcome.facts_repointed} item_facts row(s) and carried "
            f"{outcome.logos_carried} logo marker(s); dismissed {dismissed} "
            "queued job(s). A moved dismissal usually re-surfaces: its "
            "evidence hash covers whether the row is scored, and the survivor "
            "is."
        )
        if probe.both_live or probe.neither_live or probe.election_disagreed:
            summary += (
                f"; refused {probe.both_live} pair(s) whose rows BOTH resolve "
                f"by key (two real Plex items -- an operator decision), "
                f"{probe.neither_live} whose rows resolve by NEITHER (the "
                f"plex_prune sweep's population), and "
                f"{probe.election_disagreed} where the newest key is the one "
                "Plex no longer accepts -- which repeats every run until an "
                "operator resolves the identity in Plex or dismisses the row"
            )
        if outcome.skipped:
            summary += (
                f"; {outcome.skipped} pair(s) changed under the pass and were "
                "left untouched"
            )
        return summary + _tail(scan)

    return Job(
        name="plex_merge",
        interval_seconds=lambda: holder.current.scheduler.merge_days * 24 * 3600,
        run=run,
    )
```

- [ ] **Step 11: Run the merge tests and watch them pass**

```bash
docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk2-green2 test sh -c \
  "set -o pipefail; pytest tests/test_scheduler_merge_job.py tests/test_scheduler_prune_job.py -q 2>&1 | tee /app/.superpowers/run-pirk2-green2.log"
```

Expected: all pass. The prune suite runs alongside because this module imports `dismiss_jobs_for` from it — an import cycle or a signature drift shows up here and nowhere else. Then `docker rm pirk2-green2`.

- [ ] **Step 12: Commit the module**

```bash
cd /d/Sites/autoposter && \
  git add src/autoposter/scheduler/merge.py src/autoposter/config/schema.py \
          config/autoposter.example.yaml tests/test_scheduler_merge_job.py && \
  git commit -m "feat(merge): the twin-row merge job, dry run by default"
```

- [ ] **Step 13: Write the failing wiring pins**

A job absent from `SCHEDULED_JOB_NAMES` has no dashboard Run-now and answers 404; a job never appended in `app.py` never runs at all. Both have shipped broken before, which is why each gets its own pin.

Append to `tests/test_api_scheduled_runs.py`, matching the file's existing `plex_prune` test shape (same fixtures, same headers):

```python
async def test_the_twin_merge_can_be_run_from_the_dashboard(client, auth_headers, session):
    """``plex_merge`` must be in ``SCHEDULED_JOB_NAMES`` or this is a 404 and
    the operator's only way to read a dry-run report is to wait a week."""
    response = await client.post(
        "/api/scheduled-runs/plex_merge/run", headers=auth_headers
    )

    assert response.status_code == 200
    row = (
        await session.execute(
            select(ScheduledRun).where(ScheduledRun.name == "plex_merge")
        )
    ).scalar_one()
    assert row.name == "plex_merge"
```

(If the existing `plex_prune` test in that file uses different fixture names or a different assertion style, copy **that** test verbatim and change only the two job names — the file's own conventions win over the sketch above.)

Append to `tests/test_app.py`, inside the same test that already asserts `"plex_prune" in app.state.scheduler_intervals` — add one line beside it:

```python
        assert "plex_merge" in app.state.scheduler_intervals, (
            "the boot registration in app.py did not append the twin-merge job"
        )
```

and one line beside the existing `assert "plex_prune" not in app.state.scheduler_intervals` in `test_stale_job_reclaim_is_registered_even_with_the_scheduler_disabled`:

```python
        assert "plex_merge" not in app.state.scheduler_intervals
```

- [ ] **Step 14: Run them and watch them fail**

```bash
docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk2-red3 test sh -c \
  "set -o pipefail; pytest tests/test_api_scheduled_runs.py tests/test_app.py -q 2>&1 | tee /app/.superpowers/run-pirk2-red3.log"
```

Expected: the scheduled-runs pin fails with `assert 404 == 200`; the `test_app.py` pin fails on `"plex_merge" in app.state.scheduler_intervals`. Then `docker rm pirk2-red3`.

- [ ] **Step 15: Wire the job**

In `src/autoposter/api/routes.py`, add one entry to `SCHEDULED_JOB_NAMES` (`:93-102`), keeping the existing order and adding `"plex_merge"` directly after `"plex_prune"`:

```python
SCHEDULED_JOB_NAMES = frozenset({
    "collections_reconcile",
    "ratings_drift_sweep",
    "credits_scan",
    "plex_maintenance",
    "arr_sync",
    "asset_cleanup",
    "plex_prune",
    "plex_merge",
    "stale_job_reclaim",
})
```

In `src/autoposter/app.py`, add the import beside the prune one (`:58`):

```python
from autoposter.scheduler.merge import make_merge_job
from autoposter.scheduler.prune import make_prune_job
```

and append the job directly **after** the `make_prune_job(...)` block (which currently ends at `:340`), inside the same `if config.scheduler.enabled:`:

```python
            # The twin merge takes the same PlexClient the prune does, and for
            # the same reason: it asks whether a stored rating key is still
            # the item's own key, which is a section-constrained question that
            # honours the library exclusions. Built inside the factory so the
            # connect and the client construction both happen on the thread
            # the job offloads to, and read off the holder so an edited
            # exclusion list reaches it on its next run.
            #
            # `health.healthy` gates only this job's APPLY, not its scan: the
            # dry run asks Plex nothing at all, so an outage must not cost the
            # operator the report (see make_merge_job's docstring).
            scheduler_jobs.append(make_merge_job(
                holder,
                lambda: PlexClient(
                    server_factory(), holder.current.plex.excluded_libraries
                ),
                lambda: health.healthy,
            ))
```

**In the same commit, extend the run-now allowlist guard.**
`test_the_run_now_allowlist_agrees_with_the_job_factories` in
`tests/test_api_scheduled_runs.py` regexes `Job(name=...)` literals out of
`autoposter.scheduler.jobs` and `autoposter.scheduler.prune` only. `"plex_merge"`
was just added to `SCHEDULED_JOB_NAMES` above, but its `Job(name="plex_merge")`
literal lives in a **third** module, `scheduler/merge.py` — left as it stands,
this test now fails on its own message, `"in the allowlist but not the source
… ['plex_merge']"`, because the allowlist grew and the scanned source did not.
The current test (verbatim, `tests/test_api_scheduled_runs.py:145-180` on
`origin/main`):

```python
def test_the_run_now_allowlist_agrees_with_the_job_factories():
    """Roadmap row 107. ``SCHEDULED_JOB_NAMES`` is spelled out in api/routes.py
    rather than imported from the factories (importing them would pull plexapi
    and the arr/http machinery into the route module), so the two lists can
    drift -- and a job missing from the allowlist silently cannot be
    hand-triggered. This reads the ``Job(name=...)`` literals out of both
    scheduler modules' source and demands exact agreement, both directions:
    a job the allowlist misses AND a stale allowlist name with no job both
    fail here.

    The leading ``\\b`` is load-bearing: without it the pattern also matches
    inside ``table_name="media_items"`` in prune.py, which is not a job. Both
    quote styles are matched: a ``Job(name='x')`` written with single quotes
    is as real a job as the double-quoted ones, and a pattern that saw only
    double quotes would read that job's absence as agreement.
    """
    import autoposter.scheduler.jobs as jobs_module
    import autoposter.scheduler.prune as prune_module
    from autoposter.api.routes import SCHEDULED_JOB_NAMES

    source = (
        Path(jobs_module.__file__).read_text(encoding="utf-8")
        + Path(prune_module.__file__).read_text(encoding="utf-8")
    )
    declared = set(re.findall(r'\bname=["\']([a-z0-9_]+)["\']', source))
```

Change the two-module import and concatenation to three, leaving the
docstring, the regex and everything after `declared = ...` untouched:

```python
    import autoposter.scheduler.jobs as jobs_module
    import autoposter.scheduler.merge as merge_module
    import autoposter.scheduler.prune as prune_module
    from autoposter.api.routes import SCHEDULED_JOB_NAMES

    source = (
        Path(jobs_module.__file__).read_text(encoding="utf-8")
        + Path(merge_module.__file__).read_text(encoding="utf-8")
        + Path(prune_module.__file__).read_text(encoding="utf-8")
    )
    declared = set(re.findall(r'\bname=["\']([a-z0-9_]+)["\']', source))
```

(Also update the docstring's "both scheduler modules' source" to "all three
scheduler modules' source" while this is open — a one-word drift, not worth
its own step.)

- [ ] **Step 16: Run the wiring pins green**

```bash
docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk2-green3 test sh -c \
  "set -o pipefail; pytest tests/test_api_scheduled_runs.py tests/test_app.py tests/test_api_dashboard.py tests/test_config_live.py -q 2>&1 | tee /app/.superpowers/run-pirk2-green3.log"
```

Expected: all pass. `test_api_dashboard.py` and `test_config_live.py` both read `scheduler_intervals`, so a job added to that mapping shows up there — this is where an off-by-one in a job count would surface. Then `docker rm pirk2-green3`.

- [ ] **Step 17: Commit the wiring**

```bash
cd /d/Sites/autoposter && \
  git add src/autoposter/api/routes.py src/autoposter/app.py \
          tests/test_api_scheduled_runs.py tests/test_app.py && \
  git commit -m "feat(merge): register plex_merge with the scheduler and the dashboard"
```

- [ ] **Step 18: Full suite, lint, and the read-only check**

```bash
docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk2-full test sh -c \
  "set -o pipefail; ruff check . && pytest -q 2>&1 | tee /app/.superpowers/run-pirk2-full.log"
```

Expected: `ruff` clean, and a green run at the Task 1 total **plus 39** (2 in `tests/test_plex.py`, 36 in `tests/test_scheduler_merge_job.py`, 1 in `tests/test_api_scheduled_runs.py`; the two `test_app.py` additions are assertions inside existing tests, not new tests). Report the delta against the number Task 1 measured.

```bash
cd /d/Sites/autoposter && git diff --stat <the Task 1 Step 2 sha> -- src/ tests/ config/
```

Expected: the six Task 1 files plus exactly the ten files named in this task's **Files** block, and nothing else.

Tear down:

```bash
docker rm pirk2-full && \
  docker compose -p pirk2 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## Task 3: Wrap — the corrected comment, the roadmap row, the operator documentation

**Files:**
- Modify: `src/autoposter/api/action_center.py` (`_backfill_state`'s docstring, currently `:640-647`)
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (one new row; row 129's delivered note corrected)
- Modify: `deploy/README.md` (the `merge_days` cadence bullet; the new `plex_merge` section; a correction sentence in the prune section)

**Interfaces:**
- Consumes: T2's `scheduler/merge.py` (`plex_merge`, `MERGE_SOURCE = "merge"`, `MERGE_EVENT = "media_items_merged"`, `merge.apply` / `merge.max_merges` / `merge.max_merge_share`, `scheduler.merge_days`) and T1's `REKEY_SOURCE = "rekey"` / `REKEY_EVENT = "media_item_rekeyed"`. Produces nothing new.

---

- [ ] **Step 1: Correct the wrong-owner comment in `src/autoposter/api/action_center.py`**

`_backfill_state`'s docstring is the one place in the tree that tells a future reader who owns the twin rows, and it names the pruner. It has always been wrong — `plex_prune`'s "gone" test is *identity existence*, and a re-keyed item exists, which is exactly why that sweep correctly reported `pruned 0 of 16192` against a library full of twins.

Replace this paragraph (currently `:640-647`):

```python
    This is deliberately narrower than the investigation's own accounting
    (the structurally-stale "twin" rows M1a names are the pruner's territory,
    not this endpoint's -- see ``scheduler/prune.py``): a stale twin still
    reads as an ordinary unscored row here, because the only cheap read
    available -- ``media_items`` alone -- cannot tell one from a row that is
    simply next in line, and the module that CAN tell needs a live Plex probe
    (``PlexClient.exists_many``) this read-only progress read has no business
    making per request.
```

with:

```python
    This is deliberately narrower than the investigation's own accounting: a
    stale twin still reads as an ordinary unscored row here, because the only
    cheap read available -- ``media_items`` alone -- cannot tell one from a
    row that is simply next in line.

    The twins are the TWIN-MERGE job's territory (``scheduler/merge.py``), not
    the pruner's. That attribution was wrong when it was written: the prune's
    "gone" test is identity EXISTENCE, and a re-keyed item exists -- via the
    same GUID walk that forked it -- so ``plex_prune`` correctly reports zero
    against a library full of twins and could not be made to own them without
    changing what "gone" means. The merge job's own scan CAN tell, and its
    summary reports the count. That is the right surface for it, one click
    away, rather than a fourth number on this panel.
```

- [ ] **Step 2: Verify the Action Center suite still passes**

```bash
docker compose -p pirk3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk3-ac test sh -c \
  "set -o pipefail; pytest tests/test_api_action_center.py tests/test_action_flags.py -q 2>&1 | tee /app/.superpowers/run-pirk3-ac.log"
```

Expected: all pass — a docstring edit changes no behaviour, and this is the check that it really was only a docstring. Then `docker rm pirk3-ac`.

- [ ] **Step 3: Commit the correction**

```bash
cd /d/Sites/autoposter && \
  git add src/autoposter/api/action_center.py && \
  git commit -m "docs(action-center): the twins are the merge job's, not the pruner's"
```

- [ ] **Step 4: File the roadmap row**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, append one row to the numbered table (header at `:95-96`; the highest row on `origin/main` at `d7d437a` was **238** — **re-check the highest number at execution time**, because other in-flight branches file rows too, and use the next free one).

The row, with `<N>` replaced by that number:

```markdown
| <N> | `media_items` forked into twin rows whenever Plex re-keyed an item (CLOSED — the identity re-key + twin merge phase) | `media_items` is keyed on the Plex rating key, but the rating key is a *hint* the resolver is free to refuse: `PlexClient.resolve` returns the key it FOUND, and `_upsert_media_item`'s `INSERT … ON CONFLICT (rating_key)` therefore INSERTED a brand-new row whenever a re-match or a library rebuild moved the key. The original kept its render rows, its facts, its credits, its dismissals, its `logo_upload_key` and its children, and was never written again — the ~390-row unscored floor the Action Center's backfill could never clear, plus a silent loss of backup, restore, reset, revert and logo coverage for every stale row (`artwork_modes/*` all fetch by `media_items.rating_key`). Four paths produced them and two were silent: the ratings-drift sweep and the Sonarr/Radarr webhooks both built intents with **no rating key at all**, so the pipeline's fork WARNING could not fire. **delivered:** (a) an identity-shaped re-key in `process_item` — after `resolve()` and before the first upsert, the row is moved onto the live key when the database proves the key is free and exactly one row carries the resolved identity (same kind, same library, same season/episode coordinates, non-empty external-id intersection), under `SELECT … FOR UPDATE` with an `IntegrityError` fall-through so a lost race degrades to the ordinary upsert; one `events_log` row per re-key (`source = 'rekey'`, `event_type = 'media_item_rekeyed'`, both keys); (b) the drift sweep's intents now carry the row's rating key, and `arr_sync` discovery enqueues the intent built from the EXISTING row when an identity match exists under another key, turning the largest producer into a repairer; (c) `plex_merge`, a scheduled dry-run-first job in the `plex_prune` mould that reconciles the backlog — per-`art_kind` render repointing (survivor lacks it → repoint; both have it → drop the stale one) with the two counts reported separately, dismissals repointed (they honestly re-surface: the evidence hash covers whether the row is scored), facts/credits/`logo_upload_key` carried, **children repointed BEFORE the stale delete** because `parent_id` cascades, `retire`'s `(id, updated_at)` guarded delete, `dismiss_jobs_for` on BOTH keys, survivor elected by newest key in the probe-free dry run and verified live at apply. Nothing moves on disk: `naming.asset_path` is keyed by library and root folder, so a re-key rewrites no path and a merge repoints byte-identical ones — which is also why `asset_cleanup` had correctly moved nothing and needed no change. **Known limitation, deliberate:** an adopted season or episode stored its OWN external ids while `resolve()` reports the show's, so those rows are id-disjoint by construction, are NOT re-keyed at runtime, and are left to the merge job; the merge's dry run counts them separately as "no identity twin" so an operator sees the population before applying | M+M+S (three tasks) | two new sections — `merge.apply` / `max_merges` / `max_merge_share`, and `scheduler.merge_days`; no migration | 129 (the pruner — see its corrected note), 103/11a (the Action Center, whose unscored denominator this is) |
```

**One cross-reference to check before writing it.** The adjudication that
commissioned this phase asks for "rows 129/132 cross-referenced". Row **129**
is the pruner and is unambiguously the right one — its correction is the next
edit. Row **132** on `origin/main` at `d7d437a` is *"Season-poster fallback to
show art"*, which has no bearing on this phase at all. Before adding it to the
`Depends on` cell, read row 132 as it actually stands at execution time: if it
is still the season-poster row, **leave it out** and cross-reference 129 plus
the Action Center rows (103/11a) as written above; if an in-flight branch has
renumbered and 132 now names the unscored-floor or twin population, add it.
Either way, say which you found in the task report.

Then correct **row 129**'s delivered note. Find the row beginning `| 129 | Prune media_items gone from Plex |` and append this sentence to the end of its "What it is" cell, before the closing `|`:

```markdown
 **Correction (the identity re-key + twin merge phase):** an earlier reading of this row treated re-matched items as the pruner's population and proposed widening `find_prunable` to treat "resolves, but to a key another row holds" as prunable. That was the wrong remedy, and the pruner's own semantics were never wrong: its "gone" test is identity EXISTENCE, a re-keyed item exists, and `plex_prune`'s `pruned 0 of 16192` was the correct output of its own contract. Deleting the stale row is a MERGE's last step, not the whole of it — the stale row can hold the only `item_facts`, the only credits, the only `logo_upload_key` and the parent link for every season and episode beneath it. `scheduler/merge.py` (row <N>) owns that population; this sweep is unchanged and correct.
```

- [ ] **Step 5: Write the operator documentation**

In `deploy/README.md`, three edits.

**(a)** Add a cadence bullet immediately after the `prune_days` bullet (currently ending `:810`):

```markdown
- `merge_days` (default `7`) — cadence for the `media_items` twin merge: a
  pure-SQL scan for pairs of rows carrying one identity under two rating keys,
  reconciling each pair onto the surviving row. **Whether it merges is not a
  `scheduler` setting** — see `merge.apply` below, which defaults to `false`
  (dry run: report which pairs would merge). The scan itself asks Plex
  nothing, so the dry-run report is readable even while Plex is down.
```

**(b)** Add this sentence to the end of the prune section's opening paragraph (the one ending "The `plex_prune` job retires those rows.", currently `:858`):

```markdown
One of those three causes is **not** this job's, and never was: an item
**re-matched under a new rating key** still resolves — by the same GUID walk
that split its row in two — so it is not "gone" by this sweep's definition and
`plex_prune` correctly reports zero for it. That population belongs to the
twin merge below.
```

**(c)** Add a whole new section immediately after the prune section (after its `Interaction with the asset cleanup` paragraph):

````markdown
### Merging the twin rows a re-matched item left behind

Plex's rating key is a *hint*, not an identity: a re-match or a library
rebuild renumbers an item, and until the render pipeline learned to follow
that, a second `media_items` row appeared under the new key. The original kept
its render rows, its ratings and facts, its credits, its dismissals, its
uploaded-clearlogo marker and its child seasons and episodes — and was never
written again. It is why a library could sit at "16,831 of 17,242 scored"
forever while the artwork on screen looked fresh: the *twin's* render rewrote
the same file (asset paths are keyed by library and root folder, never by the
rating key), and only the twin's row got the score.

The pipeline no longer forks: when it resolves an item to a key no row holds
and exactly one row carries that identity — same kind, same library, same
season and episode numbers, and at least one external id in common — it moves
that row onto the live key and records the move in `events_log`
(`source = 'rekey'`, `event_type = 'media_item_rekeyed'`, carrying both keys).
Nothing on disk changes.

```sql
SELECT payload->>'old_rating_key', payload->>'new_rating_key',
       payload->>'title', received_at
  FROM events_log WHERE event_type = 'media_item_rekeyed'
 ORDER BY received_at DESC;
```

The `plex_merge` job reconciles the pairs that already existed. Per pair, with
the row carrying the **newer** key surviving:

- **renders** — for each art kind: the survivor lacks it → the row is
  repointed; both have it → the stale one is deleted. The two counts are
  reported **separately, and they mean opposite things**: a deleted duplicate
  shrinks the Action Center's denominator (honest accounting — that row was
  never going to be scored), while a repointed row stays in the denominator
  and becomes *finishable*, because its item now carries the live key. Do not
  read a falling total as data loss.
- **dismissals** — repointed, and most of them will come back on the queue.
  That is the dismissal contract, not a fault: a dismissal is keyed by a hash
  of the render facts the queue judges, one of which is whether the row is
  scored — and the survivor is scored where the stale row was not, so the hash
  no longer matches and the row honestly re-surfaces.
- **facts, credits and the clearlogo marker** — carried onto the survivor
  where it has none. `logo_upload_key` especially: it is what makes the logo
  revert safe, and losing it would mean that item's logo could never be
  reverted again.
- **child rows** — every season and episode under the stale row is repointed
  onto the survivor **before** it is deleted. `media_items.parent_id` cascades,
  so the other order would take live children with it.
- **queued jobs** — pending, deferred and parked `process_item` jobs are
  dismissed for **both** rating keys: an in-flight payload can name the dead
  key (it was queued before the fork) or the survivor's (the graph it was
  queued against has just changed). Running jobs are never interrupted; they
  park themselves and the next pass dismisses them.

Each merge leaves one `events_log` row (`source = 'merge'`,
`event_type = 'media_items_merged'`) carrying both identities, every count and
the carried `logo_upload_key`.

```sql
SELECT payload->>'stale_rating_key', payload->>'survivor_rating_key',
       payload->>'title', received_at
  FROM events_log WHERE event_type = 'media_items_merged'
 ORDER BY received_at DESC;
```

- `merge.apply` (default `false`) — dry run: report which pairs would merge
  and change nothing. The dry run asks Plex nothing at all, so it is readable
  during an outage.
- `merge.max_merges` (default `500`) — refuse the pass if more than this many
  twin pairs are found.
- `merge.max_merge_share` (default `0.25`) — refuse if more than this share of
  the library is, which catches the same failure on a library too small for
  the absolute cap. Only applied once there are at least 20 rows.

Five things bound what one pass can do:

1. **The applied pass refuses while Plex is unhealthy**, because every merge
   ends in a delete and the surviving row cannot be verified. The *dry run*
   deliberately does not refuse: the scan is pure SQL, and an outage must not
   cost you the one report that is always available.
2. **Every pair is verified against Plex by key before anything is deleted.**
   The dry run elects the survivor by the newer rating key; the applied pass
   asks Plex whether each row's own key is still accepted, and merges only the
   pair where the survivor's is and the stale one's is not. A pair whose rows
   **both** resolve is two real Plex items sharing one identity — a duplicate
   in your library, and your decision, not this job's. A pair where **neither**
   resolves is `plex_prune`'s population. A pair where the *older* key is the
   live one means the election was wrong about it. All three are refused and
   counted separately in the summary.
3. **A cluster of three or more rows for one identity is ambiguous** and is
   left alone, as is a pair whose keys cannot be ordered as numbers.
4. **A pair that changed under the pass is skipped whole.** Both rows are
   locked and the check runs before anything is written, so a skipped pair is
   never half-merged.
5. **The election-disagreed, ambiguous and unelectable populations are
   refused every run, not just once.** All three are re-derived identically
   on each pass and cannot resolve themselves — the same cluster or pair is
   reported forever until something outside this job changes it. The dry-run
   report names them; **resolve the identity in Plex** (so the next scan sees
   one row, not several or none orderable) or **dismiss the row** if it is
   never going to be fixed.

The job also reports two counts it is *not* responsible for, so they are not
mistaken for its work: how many unscored rows have **no identity twin at all**
(an adopted season or episode stores its own external ids while the resolver
reports the show's, so those pairs are invisible to this predicate — that is a
known, deliberate limitation), and how many pairs have **no scored render on
either row** (a different defect wearing this one's clothes).

**Nothing on disk moves, in either direction.** Both rows of a pair record
byte-identical `asset_path` strings, so a repointed render leaves its file
where it is and a deleted one leaves it too. `asset_cleanup` needs no change,
and its previous reports of "0 moved" were correct.

**The order to run this in.**

1. Deploy. The pipeline stops forking immediately.
2. Let one ratings-drift cadence and one `arr_sync` cadence pass (or press the
   Action Center's backfill), so re-matched rows get re-keyed as they are
   visited. Re-keys are visible in `events_log` with the query above.
3. Run `plex_merge` from the dashboard with `merge.apply: false` and **read
   the report.** Cross-check it against the sizing queries below.
4. Set `merge.apply: true`, run it again, and read the summary.
5. Press the Action Center's quality backfill until it reports `complete`.
   With forking stopped and the backlog merged, that is now a terminating
   process.

**Re-running the one-time adoption re-creates twins.** `python -m
autoposter.adopt` upserts directly from the live library and does not consult
the identity predicate, so an adoption run over a library this service has
already been managing will insert a row for every item whose key has moved.
That is acceptable for a one-time cutover tool — run `plex_merge` afterwards.

**Sizing it yourself, before trusting any report.** Both queries are pure SQL,
need no Plex, and are safe on a live database. The first counts the pairs; the
second splits the render outcome into repoints and drops, and its
`twin_also_unscored` column is the "neither row is scored" population the job
reports separately.

```sql
SELECT s.kind,
       count(*)                                                            AS twin_pairs,
       count(*) FILTER (WHERE t.rating_key::bigint > s.rating_key::bigint) AS newer_is_twin
  FROM media_items s
  JOIN media_items t
    ON t.id <> s.id
   AND t.kind = s.kind
   AND t.library = s.library
   AND t.season_number  IS NOT DISTINCT FROM s.season_number
   AND t.episode_number IS NOT DISTINCT FROM s.episode_number
   AND ( (s.tmdb_id IS NOT NULL AND t.tmdb_id = s.tmdb_id)
      OR (s.tvdb_id IS NOT NULL AND t.tvdb_id = s.tvdb_id)
      OR (s.imdb_id IS NOT NULL AND t.imdb_id = s.imdb_id) )
 GROUP BY 1 ORDER BY 2 DESC;
```

```sql
WITH pair AS (
  SELECT s.id AS stale_id, t.id AS twin_id
    FROM media_items s JOIN media_items t
      ON t.id <> s.id AND t.kind = s.kind AND t.library = s.library
     AND t.season_number  IS NOT DISTINCT FROM s.season_number
     AND t.episode_number IS NOT DISTINCT FROM s.episode_number
     AND ( (s.tmdb_id IS NOT NULL AND t.tmdb_id = s.tmdb_id)
        OR (s.tvdb_id IS NOT NULL AND t.tvdb_id = s.tvdb_id)
        OR (s.imdb_id IS NOT NULL AND t.imdb_id = s.imdb_id) )
)
SELECT count(*) FILTER (WHERE tr.id IS NULL)     AS would_repoint,
       count(*) FILTER (WHERE tr.id IS NOT NULL) AS would_delete,
       count(*) FILTER (WHERE tr.id IS NOT NULL
                          AND tr.quality_scored_at IS NULL) AS twin_also_unscored
  FROM pair p
  JOIN renders sr ON sr.item_id = p.stale_id
  LEFT JOIN renders tr ON tr.item_id = p.twin_id AND tr.art_kind = sr.art_kind;
```

Note that the first query counts each pair **twice** (once from each side),
and that both queries include `library` in the join because the job's identity
predicate does — a 4K copy and an HD copy of one film in two libraries are two
items and are never merged.
````

- [ ] **Step 6: Verify the documentation edits break nothing**

`tests/test_ci_path_filters.py` reads the workflow's ignore list against a set of verified non-source files, and `deploy/README.md` is prose the suite does not parse — but run the whole suite once more, because a stray backtick in a Python docstring is a syntax error and a stray pipe in a Markdown table is a silently broken row.

```bash
docker compose -p pirk3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --name pirk3-full test sh -c \
  "set -o pipefail; ruff check . && pytest -q 2>&1 | tee /app/.superpowers/run-pirk3-full.log"
```

Expected: `ruff` clean and the suite green at exactly Task 2's total — this task adds no test.

Then eyeball the two tables you edited:

```bash
cd /d/Sites/autoposter && \
  grep -c "^| " docs/superpowers/specs/2026-08-22-full-parity-roadmap.md && \
  grep -n "plex_merge\|media_items_merged\|media_item_rekeyed" deploy/README.md
```

Expected: the roadmap's row count is one higher than before your edit, and `deploy/README.md` names `plex_merge`, `media_items_merged` and `media_item_rekeyed`.

- [ ] **Step 7: Commit**

```bash
cd /d/Sites/autoposter && \
  git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md deploy/README.md && \
  git commit -m "docs: file the identity re-key and twin merge, and correct row 129"
```

- [ ] **Step 8: Final branch check and teardown**

```bash
cd /d/Sites/autoposter && \
  git diff --stat <the Task 1 Step 2 sha> -- src/ tests/ config/ docs/ deploy/ && \
  git log --oneline <the Task 1 Step 2 sha>..HEAD
```

Expected: exactly the files named across the three **Files** blocks, and six commits, none of which mentions AI, Claude or any assistant and none of which carries a `Co-Authored-By` trailer.

```bash
docker rm pirk3-full && \
  docker compose -p pirk3 -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

---

## Adjudications this plan carries forward

Named here so the implementer does not silently re-decide them, and so a
reviewer can find them.

- **A1 — `library` IS in the identity predicate.** The recon leaned the other
  way (rely on the exactly-one guard). It is in, because the failure it
  prevents is asymmetric: without it, a 4K copy and an HD copy of one film
  become a two-row cluster that the exactly-one guard merely *refuses*, which
  costs every such item its re-key forever; with it, they are correctly two
  identities and both re-key normally.
- **A2 — the adopted season/episode population is MISSED by T1 on purpose,**
  and picked up by T2's report as "no identity twin". Do not widen the
  predicate. Carrying `matched_guids` out of `resolve()` (the recon's option
  (ii)) is a separate change with its own risk and is not in this phase.
- **A4 — dismissals are repointed and re-surface.** Reported, documented, not
  worked around.
- **A5 — probe-free dry run, probed apply.** The dry run's election is the
  newest key; the apply refuses the pair the probe disagrees about and counts
  it, which is how the heuristic gets judged on evidence.
- **A6 — `dismiss_jobs_for` takes BOTH keys.** Rewriting a claimed job's
  payload is the one thing this queue never does.
- **A7 — no fourth Action Center panel number.** The merge's effect shows in
  the existing scored/scoreable/blocked line; the twin count lives in the
  job's summary, one click away.
