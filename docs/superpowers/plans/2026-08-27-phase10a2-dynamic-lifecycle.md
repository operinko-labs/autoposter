# Phase 10a-2: The Dynamic Collections Lifecycle and the CS Grammar Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `builder: dynamic` family its own lifecycle — a label-scoped
delete sweep through this service's existing delete guards, plus
delete-below-minimum — and retire the second query grammar by unifying the
Common Sense family's write path onto the raw-POST 9b grammar, under a mandatory
old-vs-new member-set equivalence proof.

**Architecture:** Six layers, each its own reviewer gate. (1) The family sweep:
the builder records what it built into the pass's `run_cache`, the engine's
`_sweep` folds that record into the set of managed titles, and everything else
— ownership label, managed row, protected labels, `delete_unconfigured`,
`max_deletes`, `dry_run`, the `EventLog` audit — is the machinery that is
already there. A family that did not run protects all of its collections
(fail-closed). (2) Delete-below-minimum, which is one line into that same
record: a key whose search matches fewer items than `params.minimum_items`
leaves the record, and the sweep does the deleting. (3) The mandatory
equivalence proof: the old plexapi `filters=` query and the new `any:`-base
query, both produced by the real code, decoded into predicate trees and
evaluated to member sets over a fixture library, for every Common Sense bucket
shape. (4) The port itself: `reconcile_content_ratings` keeps its reconciler,
its action strings, its summaries and its posters, and swaps exactly its two
write calls onto `smart.create_smart_collection` / `smart.update_smart_collection`
— plus the deliberate one-cell amendment of the golden fixture, as its own
reviewed commit. (5) The inherited-minor wave from the 10a-1 branch review.
(6) The wrap: the eleven preset re-pointings, rows 102 / 185 / 135, the operator
acceptance procedure, the PR body and the evidence sweep.

**Tech Stack:** Python 3.14, pydantic v2, plexapi 4.18.2, SQLAlchemy 2 (async),
pytest, ruff, Docker Compose. **No new runtime dependency.**

---

## Read this first — what governs this plan

Three documents bind it, and where they disagree the later one wins:

1. `.superpowers/sdd/p10a-facts.md` **C1–C8** — the phase adjudications.
2. **Addendum 2** (a USER DECISION) — the CS family's write path unifies onto
   the raw-POST 9b grammar **in this phase**, with four bindings: (a) the golden
   fixture is amended DELIBERATELY for the one documented cell, as its own
   reviewed commit, never a silent re-pin, and the recapture-then-fail guard
   stays; (b) the old-vs-new member-set equivalence proof is MANDATORY, not
   optional, oracle-grade, covering every CS bucket shape including addons and
   the other-bucket complement; (c) the one-time production migration is stated
   in the PR body; (d) roadmap row 185 CLOSES here.
3. **Addendum 3** — our empty-bucket semantics are PRESERVED: never create an
   empty bucket's collection, never delete one that already exists. Kometa's
   derive-time drop is NOT ported for CS.

The 10a-1 plan's "Phase 10a-2 — outline" (four bullets at
`docs/superpowers/plans/2026-08-27-phase10a1-dynamic-engine.md`) **predates**
Addenda 2 and 3 and its T1/T3 are superseded by them. Where this plan differs
from that outline, this plan is the one to execute. See the CONTRADICTION FLAG
section for the three places the evidence disagrees with itself.

`.superpowers/sdd/branch-review-10a1.md`'s **DEFER list** is inherited in full.
Every item on it is assigned to a task below, by name, in that task's steps —
nothing is left to "the wrap will pick it up".

---

## What this phase is NOT

- **No preset FLIPS** (C7). Not one preset's `readiness` changes. This phase
  re-points blocker TEXT only — C7 bound 10a-1 to zero catalog work; the eleven
  `DYNAMIC_ENGINE_ROW` presets stay `GATED` on row 102, and their descriptions
  move from "the engine is missing" to "10b: the preset-expansion story".
  `CATALOG_CHECKSUM` (`tests/test_collection_catalog.py:188-198`) counts only
  `readiness` and `setting`, so it must not move. The row-93 break site
  (`tests/test_collection_catalog.py:119-120`) stays unreached.
- **No re-derivation of the Common Sense family onto `derive_keys`.**
  `buckets.derive_buckets` stays, `cs_bucket.titles()` stays, the empty bucket
  keeps being returned with empty values and skipped at reconcile. That is
  Addendum 3, and it is what keeps an existing-but-now-empty CS collection out
  of every sweep's candidate set.
- **No new smart write path.** After this phase there is exactly one:
  `smart.create_smart_collection` / `smart.update_smart_collection` over a
  `build_search_url` query string. `plexapi`'s `createCollection(smart=True,
  filters=…)` and `Collection.updateFilters(...)` are not called anywhere in
  `src/` when it ends.
- **No `sync:` key.** It stays a load-time refusal; what upstream calls `sync`
  IS the sweep this phase ships, and it is governed by
  `collections.delete_unconfigured` / `max_deletes`, not by a per-definition
  boolean.
- **No enumeration-time minimum.** `params.minimum_items` is a per-KEY match
  count and it defaults to `None` (off). Upstream has no per-key minimum for
  the library types at all (`p10a-upstream-dynamic.md` §7.2) — this is a
  divergence by design and the default has to say so.

---

## Branch and cut point

- Branch name: **`feat/dynamic-collections-lifecycle`**.
- **Cut from `origin/main` after a fetch, verified with a CONTENT probe, never
  a sha probe:**

  ```bash
  git fetch origin
  git cat-file -e origin/main:src/autoposter/collections/builders/dynamic.py
  git cat-file -e origin/main:src/autoposter/collections/dynamic_types.py
  git cat-file -e origin/main:src/autoposter/collections/smart.py
  git cat-file -e origin/main:tests/oracle/9b/kometa_build_filter.py
  git cat-file -e origin/main:tests/fixtures/collections/golden_port.json
  git checkout -b feat/dynamic-collections-lifecycle origin/main
  ```

  All five probes must exit 0. **At the time this plan was written the first
  three FAIL** — 10a-1 is at `e96c552` on `feat/dynamic-engine-1` and has not
  merged. That is the expected state and the probe is the gate: if
  `dynamic.py` is not on `origin/main`, **STOP and report** that 10a-1 has not
  merged yet. Do not cut from the 10a-1 branch, and do not stack.
  Record the resolved commit (`git rev-parse HEAD`) in the Task 1 report.
- If a PR touching `src/autoposter/collections/engine.py`,
  `src/autoposter/collections/reconcile.py`,
  `src/autoposter/collections/smart.py` or
  `tests/fixtures/collections/golden_port.json` is open when this starts, say so
  in the Task 1 report and note the retarget instruction in the PR description
  (the 9a/9b/9c/10a-1 precedent), but still cut from `origin/main`.

## Execution

Executed via **superpowers:subagent-driven-development**: one fresh subagent per
task, two-stage review between tasks, this plan document **live-synced** through
every fix round (a fix that changes a signature, a message, a golden or a table
row edits the corresponding step here in the same commit, so a later task's
implementer reads the truth and not the original guess).

---

## Global Constraints

Every task's requirements implicitly include this section. These are project
law, carried verbatim from the phase brief and from the 9b/9c/10a-1 plans.

1. **Refusals RETURN from `apply`; the smart dispatch is unwrapped.**
   `engine.py:341-358` does not wrap a smart builder's `apply`. Anything an
   operator's configuration can cause once it meets a real library is caught
   inside the builder and RETURNED as an action string. A failing Plex WRITE
   still escapes, deliberately, to the per-library rollback. `REFUSALS` in
   `dynamic.py:202-211` is the tuple; `parse_filters`' bare `ValueError` is
   caught narrowly around its own call and is NOT in that tuple, because
   `pydantic.ValidationError` subclasses `ValueError`.
2. **No operator URLs or tokens anywhere** — not in logs, exceptions, action
   strings, event payloads, tracked files, probe captures or the PR body. Plex
   exception wraps are class-name-only (`type(error).__name__`). Any probe
   README carries a `<plex-host>` placeholder and describes its own scrub.
3. **The golden gate is byte-identical EXCEPT the one adjudicated cell.**
   `tests/fixtures/collections/golden_port.json` must not change in Tasks 1, 2,
   3, 5 or 6. Task 4 changes exactly the `filters` cell of every Common Sense
   collection, in **its own reviewed commit**, with the diff pasted into the
   task report. The `AUTOPOSTER_GOLDEN_CAPTURE` recapture-then-fail guard
   (`tests/test_builder_port_golden.py:418-431`) STAYS.
4. **Zero preset FLIPS.** Re-pointing text only. `CATALOG_CHECKSUM` unchanged.
5. **Container discipline.** Unique compose project per task
   (`p10a2t1` … `p10a2t6`), always with the `.superpowers/isolated-db.yml`
   overlay. Tee output to a path under `/app/.superpowers/` inside the
   container — never rely on streamed stdout. A long run uses **no `--rm`**, is
   started detached and waited on with a foreground `docker wait`, and the log
   is read back from the host afterwards. Teardown is `docker compose -p <p>
   down` — **never** `down -v`.
6. **Mutation proofs are backup + cmp.** Copy the file, edit the copy's source
   in place, run the test to see it RED, restore from the backup, `cmp` the
   restored file against the backup to prove the restore was exact, re-run to
   see GREEN. Paste real output, both halves.
7. **Commits** are conventional, `--no-gpg-sign`, staged **by name** (never
   `git add -A`), and carry **no AI attribution** of any kind.
8. **Every refusal names the way out.** A message that says only what is wrong
   is half a message.

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `src/autoposter/collections/builders/dynamic.py` | records what the family built into `run_cache`; the `generated_titles` protocol method; `minimum_items`; the family's operator report | T1, T2 |
| `src/autoposter/collections/engine.py` | `_family_state` replaces `_family_labels`; `_sweep` takes `run_cache` and folds the family record into `managed`; the report-only family branch and its N-2 noise are deleted | T1 |
| `src/autoposter/collections/smart.py` | `count_matches` extracted from `require_matches` | T2 |
| `tests/oracle/10a2/plexapi_side.py` | drives plexapi's real `_buildSearchKey` for each CS bucket shape | T3 |
| `tests/oracle/10a2/ours_side.py` | drives `parse_filters` + `build_search_url` for the same shapes | T3 |
| `tests/oracle/10a2/member_sets.py` | the query-string → predicate-tree decoder and the member-set evaluator, with its premises cited | T3 |
| `tests/test_collection_cs_equivalence.py` | the gate: identical member sets, both library types, every bucket shape | T3 |
| `src/autoposter/collections/reconcile.py` | the CS write path: `create_smart_collection`/`update_smart_collection`, the URL-folding hash, the per-bucket refusal containment | T4 |
| `src/autoposter/collections/builders/cs_bucket.py` | passes the pass-scoped `LibraryTagResolver` down | T4 |
| `tests/test_builder_port_golden.py` | harness: the POST route records the uri, `FakeChoice` gains a key | T4 |
| `tests/fixtures/collections/golden_port.json` | the one adjudicated cell, per CS collection | T4 |
| `src/autoposter/collections/catalog.py` | eleven blocker-text re-pointings, no readiness change | T6 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | rows 102, 185, 135 | T6 |

---

## Task 1: The label-scoped family sweep

**Files:**
- Modify: `src/autoposter/collections/builders/dynamic.py` (add the run-cache
  record and the `generated_titles` protocol method)
- Modify: `src/autoposter/collections/engine.py:664-684` (`_family_labels` →
  `_family_state`), `:687-852` (`_sweep`), `:393-398` (the call site)
- Test: `tests/test_builder_knobs.py` (the sweep's home),
  `tests/test_builder_dynamic.py` (the record)

**Interfaces:**
- Produces, in `dynamic.py`:
  - `def generated_titles(run_cache: dict, definition) -> set[str] | None`
    (module function) and `DynamicBuilder.generated_titles(self, run_cache,
    definition) -> set[str] | None` (the registry protocol method, mirroring
    `family_label`). `None` means "this family did not get as far as deciding".
  - `def _generated_key(label: str) -> str` — `"dynamic:generated:%s" % label`.
- Produces, in `engine.py`:
  - `def _family_state(definitions, library, run_cache) -> tuple[dict[str,str],
    dict[str, set[str]]]` — `({label: definition title}, {label: titles built
    this pass})`. Replaces `_family_labels`, which has no other caller.
  - `def _why(family_title: str | None) -> str`.
  - `_sweep(..., run_cache: dict)` — one new keyword argument.
- Consumes: `reconcile.has_label` / `load_labels` / `protected_label`, already
  imported by `engine.py:52`.

**Background the implementer needs.** Kometa's `sync: true` labels every
generated collection with the family label and then deletes labelled
collections no key regenerated this pass (`meta.py:1300`, `:1428`,
`:1456-1461`). 10a-1 shipped the labelling half: every collection a `dynamic`
definition creates carries `FAMILY_LABEL_PREFIX + definition.title`
(`dynamic.py:114`, `:540-542`) beside the ownership label, and `_sweep` reports
such members and refuses to consider them (`engine.py:768-784`). This task
ships the other half — **through our guards, never a bare `library.delete`**
(adjudication C4). The mechanism is deliberately *subtractive*: the family's
generated titles join the `managed` set the sweep already computes, and every
guard below that point is the one that is already there.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_builder_knobs.py`. Note `FakeSection` there answers
`listFilterChoices` with `[]`, so a real `dynamic` definition refuses at the
family level — which is exactly what the fail-closed test wants. The two tests
that need a family that DID run seed the record directly, which is also how a
reviewer reads what the record means.

```python
async def test_a_family_member_this_pass_did_not_build_is_swept(session):
    """The other half of Kometa's `sync:` (meta.py:1456-1461), through OUR
    guards. A collection carrying the family label that this pass's enumeration
    no longer produces is an orphan of exactly the kind the sweep exists for --
    the operator narrowed `include:`, or the library stopped holding the value.
    """
    from autoposter.collections.builders.dynamic import (
        _generated_key, family_label,
    )

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"},
    )
    kept = FakeCollection(
        "Top Horror movies", [FakeItem("m1")],
        labels=[LABEL, family_label(definition)],
    )
    gone = FakeCollection(
        "Top Western movies", [FakeItem("m1")],
        labels=[LABEL, family_label(definition)],
    )
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[kept, gone])
    for title in ("Top Horror movies", "Top Western movies"):
        session.add(ManagedCollection(
            library="Movies", title=title, kind="smart",
            plex_rating_key="c-" + title, definition_hash="seed",
        ))
    await session.flush()

    run = await run_library(
        session, section, "Movies", "Movie", [definition],
        _config(delete_unconfigured=True), sweep=True,
        run_cache_seed={
            _generated_key(family_label(definition)): {"Top Horror movies"},
        },
    )

    assert kept.deleted is False
    assert gone.deleted is True
    assert (
        "deleted 'Top Western movies': the 'Genres' family no longer builds it"
        in run.actions
    )
    assert not any("Top Horror movies" in one for one in run.actions), (
        "a healthy member emits nothing -- fifty members would otherwise be "
        "fifty lines of 'nothing was deleted' on every sweep-enabled pass"
    )
    assert (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.title == "Top Western movies"
            )
        )
    ).scalar_one_or_none() is None


async def test_a_family_that_did_not_run_protects_every_one_of_its_collections(
    session,
):
    """FAIL-CLOSED, and the reason this is a record rather than a re-derivation.

    A pass where the definition was outside its schedule, or refused (a library
    type it cannot serve, an empty enumeration, an all-excluded family, an
    over-cap fan-out, a duplicate title, a Plex read that failed) has NOT
    decided that the operator narrowed the family -- it has decided nothing.
    Deleting on that would turn one transient `listFilterChoices` failure into
    a deleted family. One line for the family, not one per member.
    """
    from autoposter.collections.builders.dynamic import family_label

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"},
    )
    orphan = FakeCollection(
        "Top Horror movies", [FakeItem("m1")],
        labels=[LABEL, family_label(definition)],
    )
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[orphan])
    session.add(ManagedCollection(
        library="Movies", title="Top Horror movies", kind="smart",
        plex_rating_key="c-Top Horror movies", definition_hash="seed",
    ))
    await session.flush()

    # No seed: this library reports no genres at all, so the definition refuses
    # at the family level and records nothing.
    run = await run_library(
        session, section, "Movies", "Movie", [definition],
        _config(delete_unconfigured=True), sweep=True,
    )

    assert orphan.deleted is False
    assert any(
        "did not build anything this pass" in one and "Genres" in one
        for one in run.actions
    ), run.actions
    assert (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.title == "Top Horror movies"
            )
        )
    ).scalar_one_or_none() is not None


async def test_a_family_member_without_the_ownership_label_is_never_swept(
    session,
):
    """T5 review Minor 3, closed. The 10a-1 branch put the family check ABOVE
    the ownership check, which was harmless while the branch only reported.
    Now that it deletes, a collection carrying the family label but NOT ours --
    an operator labelled it by hand, or stripped our label -- must fall out at
    the same boundary every other candidate falls out at."""
    from autoposter.collections.builders.dynamic import (
        _generated_key, family_label,
    )

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"},
    )
    theirs = FakeCollection(
        "Top Western movies", [FakeItem("m1")],
        labels=[family_label(definition)],
    )
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[theirs])
    session.add(ManagedCollection(
        library="Movies", title="Top Western movies", kind="smart",
        plex_rating_key="c-Top Western movies", definition_hash="seed",
    ))
    await session.flush()

    run = await run_library(
        session, section, "Movies", "Movie", [definition],
        _config(delete_unconfigured=True), sweep=True,
        run_cache_seed={_generated_key(family_label(definition)): set()},
    )

    assert theirs.deleted is False
    assert not any("deleted" in one for one in run.actions), run.actions


async def test_a_protected_label_beats_the_family_sweep(session):
    """Protected wins over everything, as it does for every other candidate."""
    from autoposter.collections.builders.dynamic import (
        _generated_key, family_label,
    )

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"},
    )
    protected = FakeCollection(
        "Top Western movies", [FakeItem("m1")],
        labels=[LABEL, family_label(definition),
                "Collection managed by Maintainerr"],
    )
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[protected])
    session.add(ManagedCollection(
        library="Movies", title="Top Western movies", kind="smart",
        plex_rating_key="c-Top Western movies", definition_hash="seed",
    ))
    await session.flush()

    run = await run_library(
        session, section, "Movies", "Movie", [definition],
        _config(delete_unconfigured=True, protect_labels=[
            "Collection managed by Maintainerr",
        ]), sweep=True,
        run_cache_seed={_generated_key(family_label(definition)): set()},
    )

    assert protected.deleted is False
    assert any("protected" in one for one in run.actions), run.actions


async def test_a_family_sweep_is_reported_not_performed_when_not_opted_in(
    session,
):
    """`delete_unconfigured` is off by default, and off means reported -- the
    same posture every other candidate gets, and the message names the setting
    that would act on it."""
    from autoposter.collections.builders.dynamic import (
        _generated_key, family_label,
    )

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"},
    )
    gone = FakeCollection(
        "Top Western movies", [FakeItem("m1")],
        labels=[LABEL, family_label(definition)],
    )
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[gone])
    session.add(ManagedCollection(
        library="Movies", title="Top Western movies", kind="smart",
        plex_rating_key="c-Top Western movies", definition_hash="seed",
    ))
    await session.flush()

    run = await run_library(
        session, section, "Movies", "Movie", [definition],
        _config(), sweep=True,
        run_cache_seed={_generated_key(family_label(definition)): set()},
    )

    assert gone.deleted is False
    assert (
        "'Top Western movies' is no longer built by the 'Genres' family; "
        "set collections.delete_unconfigured to delete it" in run.actions
    )
```

And in `tests/test_builder_dynamic.py`, the record itself:

```python
async def test_a_family_records_every_title_it_derived_for_the_sweep(session):
    """The record is the sweep's whole input, so what goes into it is a
    promise: EVERY title the family derived, written before a single collection
    is created. A key whose write refuses is still a key this family builds."""
    from autoposter.collections.builders.dynamic import _generated_key

    definition = _definition()
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    assert ctx.run_cache[_generated_key(family_label(definition))] == {
        "Top Horror movies", "Top Drama movies",
    }


async def test_a_family_level_refusal_records_nothing_at_all(session):
    """The fail-closed half, at its source: a definition that refuses before it
    derives anything leaves NO record, and ``generated_titles`` answers None --
    which the sweep reads as "do not consider this family's collections"."""
    from autoposter.collections.builders.dynamic import generated_titles

    definition = _definition()
    section = FakeSection(choices=[])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert actions and actions[0].startswith("refused")
    assert generated_titles(ctx.run_cache, definition) is None


def test_the_builder_offers_the_sweep_its_generated_record_by_protocol():
    """The engine asks the REGISTRY entry rather than importing this module --
    the same shape ``family_label`` established, so a future family builder can
    join the sweep by growing two methods and nothing in the engine changes."""
    builder = REGISTRY["dynamic"]
    assert callable(getattr(builder, "family_label", None))
    assert callable(getattr(builder, "generated_titles", None))
    assert builder.generated_titles({}, _definition()) is None
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p p10a2t1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_knobs.py tests/test_builder_dynamic.py 2>&1 | tee /app/.superpowers/run-t1-red1.log; echo EXIT=$?'
```

Expected: the `run_cache_seed` tests fail with
`TypeError: run_library() got an unexpected keyword argument 'run_cache_seed'`;
the `dynamic.py` tests fail with
`ImportError: cannot import name '_generated_key'` /
`AttributeError: 'DynamicBuilder' object has no attribute 'generated_titles'`.

- [ ] **Step 3: Add the record and the protocol method to `dynamic.py`**

After the `family_label` function (`dynamic.py:214-216`), add:

```python
def _generated_key(label: str) -> str:
    """The pass's run-cache key for one family's generated titles.

    Keyed on the family LABEL rather than the definition title, because the
    label is what the sweep has in hand when it finds a member: it matched the
    collection on that label a line earlier.
    """
    return "dynamic:generated:%s" % label


def generated_titles(run_cache: dict, definition) -> set[str] | None:
    """What ``definition``'s family built this pass, or ``None``.

    ``None`` is the FAIL-CLOSED answer and it means "this family did not get as
    far as deciding": the definition was outside its schedule, or it refused at
    the family level -- a library type its type cannot serve, an empty
    enumeration, an all-excluded family, an over-cap fan-out, a duplicate title,
    a dead filter lookup. A sweep must not delete a family's collections on a
    pass that never enumerated the library: one transient ``listFilterChoices``
    failure would otherwise read as "the operator narrowed the family" and take
    every collection in it.

    A set (not ``None``) is the family's own answer, and it is the set of every
    title the family DERIVED -- written before a single collection is created,
    so a key whose Plex write refused is still a key this family builds.
    """
    return run_cache.get(_generated_key(family_label(definition)))
```

Add `"generated_titles"` to `__all__` (`dynamic.py:107`), keeping it sorted the
way that list already is:

```python
__all__ = [
    "FAMILY_LABEL_PREFIX", "DynamicBuilder", "DynamicParams", "family_label",
    "generated_titles",
]
```

Add the protocol method to `DynamicBuilder`, immediately after `family_label`
(`dynamic.py:447-454`):

```python
    def generated_titles(self, run_cache: dict, definition) -> set[str] | None:
        """What this definition's family built this pass -- see the module
        function of the same name for what ``None`` means.

        A method as well as a module function for ``family_label``'s reason:
        ``engine._sweep`` has a builder and a definition and no reason to import
        this module by name. Growing these two methods is the whole protocol a
        future family builder needs to join the sweep.
        """
        return generated_titles(run_cache, definition)
```

And write the record in `apply`, immediately after the `max_collections` check
(after `dynamic.py:533`, before the `settings = definition.model_copy(...)`
line):

```python
        # The sweep's input, written HERE -- after the family-level refusals,
        # before a single collection is created. Seeded with every title the
        # family derived, so a key whose write refuses below is still a key this
        # family builds and its collection survives the sweep: a Plex write that
        # failed is not an operator narrowing their family. The set is
        # deliberately MUTABLE and is the object the sweep reads, so a per-key
        # decision below can take a title back out of it in place.
        generated: set[str] = {unit.title for unit in titled}
        ctx.run_cache[_generated_key(family_label(definition))] = generated
```

- [ ] **Step 4: Replace `_family_labels` with `_family_state` in `engine.py`**

Replace the whole of `_family_labels` (`engine.py:664-684`) with:

```python
def _family_state(
    definitions: list[CollectionDefinition], library: str, run_cache: dict
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """``({label: definition title}, {label: what it built this pass})``.

    Pure -- it reads the registry, the definitions and the pass's scratch, never
    Plex -- and library-scoped for ``definition_titles_for``'s reason: a family
    aimed at another library must not protect, or sweep, this one's collections.

    The builder is asked rather than the module imported, so the engine keeps
    knowing only the protocol: a builder that manages a family whose titles it
    cannot enumerate offline says so by having a ``family_label``, and says what
    it actually built by having a ``generated_titles``.

    A family ABSENT from the second mapping is the fail-closed state: it did not
    run this pass (outside its schedule) or it refused before deriving anything,
    and the sweep must not consider any of its collections.
    """
    labels: dict[str, str] = {}
    generated: dict[str, set[str]] = {}
    for definition in definitions:
        if not _targets(definition, library):
            continue
        builder = REGISTRY[definition.builder]
        namer = getattr(builder, "family_label", None)
        if namer is None:
            continue
        label = namer(definition)
        labels[label] = definition.title
        reader = getattr(builder, "generated_titles", None)
        built = reader(run_cache, definition) if reader is not None else None
        if built is not None:
            generated[label] = built
    return labels, generated


def _why(family_title: str | None) -> str:
    """Why one candidate is being swept, as the fragment the three delete
    messages share. A family member and an ordinary orphan are the same kind of
    thing to every guard below and a different thing to the operator reading the
    report -- the collection is gone either way, but only one of them is
    something they can put back by widening ``include:``."""
    return (
        "the %r family no longer builds it" % family_title
        if family_title else "no definition builds it"
    )
```

- [ ] **Step 5: Rewrite `_sweep`'s family handling**

Change the signature (`engine.py:687-697`) to take the pass's scratch:

```python
async def _sweep(
    session: AsyncSession,
    section,
    library: str,
    library_type: str,
    definitions: list[CollectionDefinition],
    config,
    label: str,
    dry_run: bool,
    listing,
    run_cache: dict,
) -> list[DefinitionResult]:
```

Replace the fifth bullet of its docstring (`engine.py:718-722`, "a **dynamic
family's member** is reported and never deleted …") with:

```
    - a **dynamic family's members** are enumerated by the family LABEL, which
      is Kometa's own handle for the same job (``append_label``, meta.py:1421,
      and the ``sync:`` sweep at :1300, :1456-1461). The ones the family
      REBUILT this pass are managed and are not candidates; the ones it did not
      are candidates like any other, through every guard above. A family that
      did not build anything this pass -- outside its schedule, or refused --
      protects all of its collections and says so once, for the family rather
      than once per member: the alternative turns one failed Plex read into a
      deleted family.
```

Replace the body from `families = _family_labels(definitions, library)`
(`engine.py:733`) with:

```python
    families, generated = _family_state(definitions, library, run_cache)
```

Immediately after the `rows = {...}` block and the `results` / `candidates`
declarations (`engine.py:743-744`), insert the once-per-family report:

```python
    for family, family_title in families.items():
        if family in generated:
            continue
        # One line for the FAMILY, not one per member. A healthy fifty-member
        # family that happened to be outside its schedule would otherwise emit
        # fifty identical lines saying nothing was deleted.
        results.append(_swept(family_title, library, (
            "the dynamic family %r did not build anything this pass -- it was "
            "outside its schedule, or it refused -- so none of its collections "
            "were considered for deletion" % family_title
        )))
```

Replace the family branch inside the loop (`engine.py:768-784`) with:

```python
        family = next(
            (one for one in families if has_label(collection, one)), None
        )
        family_title: str | None = None
        if family is not None:
            if family not in generated or title in generated[family]:
                # Either the family did not run (reported once, above) or this
                # is a member it just rebuilt. Nothing to say per member.
                continue
            family_title = families[family]
        if not has_label(collection, label):
            logger.info(
                "%s: %r has a managed row but not the %r label; not ours to delete",
                library, title, label,
            )
            continue
        candidates.append((title, collection, rows[title], family_title))
```

Note the ORDER: the family check now sits *above* the ownership check only for
the "skip it" decision; a family member that is a candidate still has to carry
the ownership label to reach `candidates`. That is T5 review Minor 3, closed.

Then thread `family_title` through the three message sites. The
`delete_unconfigured`-off block (`engine.py:793-799`):

```python
    if not config.collections.delete_unconfigured:
        for title, _, _, family_title in candidates:
            results.append(_swept(title, library, (
                "%r is no longer built by the %r family; "
                "set collections.delete_unconfigured to delete it"
                % (title, family_title)
                if family_title else
                "%r is no longer built by any definition; "
                "set collections.delete_unconfigured to delete it" % title
            )))
        return results
```

The delete loop (`engine.py:812-851`) — only the four highlighted strings and
the tuple unpacking change; every guard, the `EventLog`, and the per-candidate
`flush` stay exactly as they are:

```python
    for title, collection, row, family_title in candidates:
        why = _why(family_title)
        if dry_run:
            results.append(_swept(
                title, library, "would delete %r: %s" % (title, why), 1
            ))
            continue
        try:
            collection.delete()
        except Exception:
            logger.exception("%s: could not delete %r", library, title)
            results.append(_swept(
                title, library, "failed to delete %r: see logs for detail" % title
            ))
            continue
        await session.delete(row)
        session.add(EventLog(
            source="collections",
            event_type="collection_deleted",
            payload={
                "library": library,
                "title": title,
                "rating_key": str(getattr(collection, "ratingKey", "") or ""),
            },
            outcome="deleted; %s" % why,
        ))
        await session.flush()
        results.append(_swept(
            title, library, "deleted %r: %s" % (title, why), 1
        ))
    return results
```

The non-family strings are byte-identical to what shipped
(`"would delete %r: no definition builds it"`,
`"deleted %r: no definition builds it"`, `"deleted; no definition builds it"`),
which is what keeps `tests/test_api_collections_builders.py:419`,
`tests/test_builder_knobs.py:381`, `:497` and `:711` green.

Finally, the call site (`engine.py:393-398`):

```python
            swept = await _sweep(
                session, section, library, library_type, definitions, config,
                label=label, dry_run=dry_run, listing=listing,
                run_cache=run_cache,
            )
```

- [ ] **Step 6: Add the `run_cache_seed` test seam to `run_library` /
      `run_definitions`**

The tests need to seed the pass's scratch without a real enumeration.
`run_definitions` builds `run_cache` as a local (`engine.py:290`); give both
public entry points one optional keyword. Add to `run_library`'s signature
(after `cache: ProviderCache | None = None`) and to `run_definitions`':

```python
    run_cache_seed: dict | None = None,
```

Document it once, in `run_definitions`' docstring, and forward it from
`run_library`:

```
    ``run_cache_seed`` pre-populates the pass's scratch. It exists for the
    delete sweep's tests, which need a dynamic family's generated-titles record
    without a real enumeration behind it; production never passes one, and a
    real pass overwrites any key a builder owns.
```

At `engine.py:290`:

```python
    run_cache: dict = dict(run_cache_seed or {})
```

- [ ] **Step 7: Rewrite the 10a-1 report-only test**

`tests/test_builder_knobs.py:601-644`
(`test_a_dynamic_familys_collections_are_reported_by_the_sweep_never_deleted`)
asserts `"10a-2" in one` — the phase that is now shipping. Its SUBSTANCE
survives (that section reports no genres, so the family refuses and records
nothing, so the orphan is protected), so replace the name, the docstring and
the one assertion rather than deleting the test:

```python
async def test_a_family_whose_enumeration_failed_keeps_all_its_collections(
    session,
):
    """The 10a-1 report-only branch, replaced by the real sweep. This library
    reports no genres at all, so the definition refuses at the family level and
    records nothing -- and a pass that decided nothing must delete nothing.
    Kept as its own test beside the seeded ones because it is the only one that
    reaches the fail-closed state through a real refusal rather than through an
    absent seed."""
    from autoposter.collections.builders.dynamic import family_label

    definition = CollectionDefinition(
        title="Genres", builder="dynamic", params={"type": "genre"},
    )
    orphan = FakeCollection(
        "Top Horror movies", [FakeItem("m1")],
        labels=[LABEL, family_label(definition)],
    )
    section = FakeSection([("m1", ["imdb://tt1"])], existing=[orphan])
    session.add(ManagedCollection(
        library="Movies", title="Top Horror movies", kind="smart",
        plex_rating_key="c-Top Horror movies", definition_hash="seed",
    ))
    await session.flush()

    run = await run_library(
        session, section, "Movies", "Movie", [definition],
        _config(delete_unconfigured=True), sweep=True,
    )

    assert orphan.deleted is False
    assert any("did not build anything this pass" in one for one in run.actions)
    assert (
        await session.execute(
            select(ManagedCollection).where(
                ManagedCollection.title == "Top Horror movies"
            )
        )
    ).scalar_one_or_none() is not None
```

Also update `dynamic.py`'s module docstring, which still says the engine's
sweep "REPORTS family members and never considers them for deletion" and names
10a-2 in the future tense (`dynamic.py:41-44`, and the `sync` refusal at
`:136-143`). Rewrite both to the shipped truth:

```python
**The family label.** Every collection this builder creates carries
``FAMILY_LABEL_PREFIX + definition.title`` beside the ownership label. That is
Kometa's own handle for the same job -- it labels each generated collection with
the map name (``append_label``, meta.py:1421) and its ``sync:`` sweep deletes
labelled collections no key regenerated (meta.py:1300, :1456-1461) -- and it is
what ``engine._sweep`` enumerates, through this service's own delete guards.
This module's half of that is ``generated_titles``: the pass's record of every
title the family derived, written before anything is created, so a Plex write
that failed cannot read as an operator narrowing their family. No record at all
means the family did not decide, and the sweep then considers none of its
collections.
```

and, in `_REFUSED_KEYS["sync"]`, replace the final two sentences:

```python
        "`sync: true` upstream is a DELETE sweep -- it labels each generated "
        "collection and deletes labelled collections no key regenerated "
        "(meta.py:1300, :1456-1461) -- not a sync mode. This service does that "
        "sweep already, for every builder rather than for this one, and gates "
        "it on `collections.delete_unconfigured` and `collections.max_deletes` "
        "instead of on a per-definition boolean -- so a config mistake cannot "
        "cascade into a wiped library one definition at a time"
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
docker compose -p p10a2t1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_knobs.py tests/test_builder_dynamic.py tests/test_api_collections_builders.py 2>&1 | tee /app/.superpowers/run-t1-green1.log; echo EXIT=$?'
```

Expected: all pass, `EXIT=0`.

- [ ] **Step 9: The mutation proof**

Three mutations, each backup + cmp (global constraint 6). Paste all six halves
of real output into the task report.

- **Mutation A — the fail-closed rule.** In `_family_state`, change
  `if built is not None:` to `if True:` and `built` to `built or set()`.
  Expected RED: `test_a_family_that_did_not_run_protects_every_one_of_its_collections`
  and `test_a_family_whose_enumeration_failed_keeps_all_its_collections` both
  fail on `orphan.deleted is False`.
- **Mutation B — the ownership boundary.** In `_sweep`, move
  `candidates.append(...)` above the `if not has_label(collection, label)`
  guard. Expected RED:
  `test_a_family_member_without_the_ownership_label_is_never_swept`.
- **Mutation C — the record's seed.** In `dynamic.py`'s `apply`, change
  `generated: set[str] = {unit.title for unit in titled}` to `= set()`.
  Expected RED:
  `test_a_family_records_every_title_it_derived_for_the_sweep`, and
  `test_a_family_member_this_pass_did_not_build_is_swept` stays green (it
  seeds the record directly) — note that asymmetry in the report, it is why
  both kinds of test are there.

```bash
cp src/autoposter/collections/engine.py /tmp/engine.bak
# ... edit, run, restore ...
cp /tmp/engine.bak src/autoposter/collections/engine.py
cmp /tmp/engine.bak src/autoposter/collections/engine.py && echo RESTORED-EXACT
```

- [ ] **Step 10: Gate and commit**

```bash
docker compose -p p10a2t1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10a2t1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-t1-golden.log; echo EXIT=$?'
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
docker compose -p p10a2t1 down
```

The golden run and the empty `git diff --stat` are the standing proof of global
constraint 3 for this task (the sweep is off by default, so the golden
scenarios never reach it — assert that rather than assume it).

```bash
git add src/autoposter/collections/builders/dynamic.py \
        src/autoposter/collections/engine.py \
        tests/test_builder_dynamic.py tests/test_builder_knobs.py
git commit --no-gpg-sign -m "feat(collections): the dynamic family's delete sweep

A collection carrying a family's label that the family did not rebuild this
pass is a sweep candidate like any other -- ownership label, managed row,
protected labels, delete_unconfigured, max_deletes, dry_run and the EventLog
audit are all the machinery that was already there. The builder records what it
derived into the pass's run_cache before it writes anything, so a Plex write
that failed cannot read as an operator narrowing their family; no record at all
means the family decided nothing and none of its collections are considered.
Closes the 10a-1 review's Minor N-2 (fifty lines per healthy family) and T5's
Minor 3 (the family branch sat above the ownership check)."
```

---

## Task 2: Delete-below-minimum, and what the family tells the operator

**Files:**
- Modify: `src/autoposter/collections/smart.py:103-131` (extract `count_matches`)
- Modify: `src/autoposter/collections/builders/dynamic.py` (`minimum_items`, the
  below-minimum branch, the two report additions)
- Test: `tests/test_collection_smart.py`, `tests/test_builder_dynamic.py`

**Interfaces:**
- Consumes: `dynamic._generated_key`, the mutable `generated` set written by
  Task 1.
- Produces:
  - `smart.count_matches(section, url) -> int` — how many items the filter
    matches now, zero included. `require_matches` becomes a wrapper that
    refuses at zero.
  - `DynamicParams.minimum_items: int | None = Field(default=None, ge=1)`.

**Background.** Roadmap decomposition step 4 names "delete-below-minimum" and
this is it — but **upstream has no per-key minimum for the library dynamic
types at all** (`p10a-upstream-dynamic.md` §7.2; the nearest thing is the
people types' `data: minimum` credit threshold, which filters the ENUMERATION,
not the result). So this is a divergence by design, its default is `None` and
not a number, and the field's docstring has to say both. It rides Task 1's
sweep rather than deleting anything itself: a below-minimum key is simply not
in the pass's generated record, and the sweep does what the sweep does.

- [ ] **Step 1: Write the failing tests**

In `tests/test_collection_smart.py`:

```python
def test_counting_matches_does_not_refuse_at_zero():
    """``require_matches`` refuses at zero because a permanently-empty smart
    collection is not a collection. A per-key MINIMUM asks a different
    question -- "how many, so I can compare" -- and zero is a legitimate answer
    to it. Two functions rather than a flag, so neither caller can be read as
    the other."""
    section = _FakeSection(items=[])
    assert count_matches(section, "?type=1&genre=1138") == 0


def test_counting_matches_still_wraps_a_dead_plex_class_name_only():
    """The wrap is on the counting half, so both callers inherit it and neither
    can leak a tokenised URL by being the one that forgot."""
    section = _RaisingSection()
    with pytest.raises(SmartCollectionUnavailable) as caught:
        count_matches(section, "?type=1&genre=1138")
    assert "https://" not in str(caught.value)
    assert "plex-token" not in str(caught.value)
```

In `tests/test_builder_dynamic.py`:

```python
def test_minimum_items_is_off_by_default_and_says_it_is_a_divergence():
    """Upstream has NO per-key minimum for the library dynamic types
    (p10a-upstream-dynamic.md 7.2), so the default cannot be a number an
    operator would have to discover and turn off."""
    assert DynamicParams(type="genre").minimum_items is None
    with pytest.raises(ValidationError):
        DynamicParams(type="genre", minimum_items=0)


async def test_a_key_below_the_minimum_is_not_created(session):
    definition = _definition(params={"type": "genre", "minimum_items": 4})
    section = FakeSection()   # every search answers with three items
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert section._existing == {}
    assert any(
        "matches 3 item(s)" in one and "minimum_items" in one and "4" in one
        for one in actions
    ), actions


async def test_a_key_below_the_minimum_leaves_the_sweeps_record(session):
    """The whole of delete-below-minimum: the key drops out of the pass's
    generated record and the family sweep -- one mechanism, through the same
    guards -- deletes the collection if one already exists. Nothing here
    deletes anything itself."""
    from autoposter.collections.builders.dynamic import _generated_key

    definition = _definition(params={"type": "genre", "minimum_items": 4})
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    assert ctx.run_cache[_generated_key(family_label(definition))] == set()


async def test_a_key_at_the_minimum_is_created(session):
    """The boundary is inclusive: `minimum_items: 3` means three is enough."""
    definition = _definition(params={"type": "genre", "minimum_items": 3})
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    assert set(section._existing) == {"Top Horror movies", "Top Drama movies"}


async def test_no_minimum_costs_no_extra_plex_read(session):
    """The count is a Plex round trip per key. It is paid only by an operator
    who asked for it -- ``reconcile_smart_collection`` does its own C8 probe on
    the create/update path, and doing it twice for every family in every pass
    would double the read cost of the default configuration."""
    definition = _definition()
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    # Two keys, one probe each, from reconcile_smart_collection's own C8 gate.
    assert len(section.fetched) == 2


async def test_the_absent_value_key_is_reported_rather_than_silently_dropped(
    session,
):
    """10a-1 review, T4's deferred minor. ``ABSENT_KEY`` is Plex's "these items
    have no value for this field" and ``family_titles`` drops it, which is
    right -- but silently, so an operator whose library has 40 unrated films
    sees a family with no bucket for them and no reason why. And a real tag
    named literally "None" is indistinguishable from the sentinel at this
    layer, so the report has to say that too rather than pretend to know."""
    definition = _definition()
    section = FakeSection(choices=[
        FakeChoice("1138", "Horror"), FakeChoice("None", "None"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert any(
        "'None'" in one and "no value" in one and "title_override" not in one
        for one in actions
    ), actions
    assert "Top None movies" not in section._existing


async def test_narrowing_entries_the_library_never_reports_are_named(session):
    """10a-1 review, Minor N-5. An ``include:``/``exclude:``/``addons:``/
    override entry naming a key the library does not hold is silently inert --
    upstream parity, and the all-excluded family does refuse, so this is not a
    correctness hole. It is a typo an operator cannot see: ``include: [Horor]``
    builds a family with one fewer collection and says nothing."""
    definition = _definition(params={
        "type": "genre",
        "include": ["Horror", "Horor"],
        "key_name_override": {"Wsetern": "Western"},
    })
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    inert = [one for one in actions if "names no value" in one]
    assert len(inert) == 1, actions
    assert "'Horor'" in inert[0] and "include" in inert[0]
    assert "'Wsetern'" in inert[0] and "key_name_override" in inert[0]
    assert "'Horror'" not in inert[0]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p p10a2t2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_smart.py tests/test_builder_dynamic.py 2>&1 | tee /app/.superpowers/run-t2-red1.log; echo EXIT=$?'
```

Expected: `ImportError: cannot import name 'count_matches'`, and
`ValidationError: Extra inputs are not permitted [minimum_items]`.

- [ ] **Step 3: Extract `count_matches` in `smart.py`**

Replace `require_matches` (`smart.py:103-131`) with the pair. Add
`"count_matches"` to `__all__` (`smart.py:63-72`), keeping it sorted.

```python
def count_matches(section, url: str) -> int:
    """How many items ``url`` matches right now. Zero is an answer, not a fault.

    Split out of ``require_matches`` because the dynamic engine's
    ``minimum_items`` asks a different question of the same read: "how many, so
    I can compare" rather than "is this collection worth creating at all". Two
    functions rather than one with a flag, so a reader of either call site
    cannot mistake it for the other.

    The catch is blanket and class-name-only, which is ``plex_search.build``'s
    own reasoning (:342-359): any failure of this call ends the caller's
    decision the same way, and nothing is memoised here that a coding bug could
    be mistaken for a library fact. It lives on THIS half so both callers
    inherit it.
    """
    try:
        items = section.fetchItems("/library/sections/%s/all%s" % (section.key, url))
    except Exception as error:  # class name only, never the message
        raise SmartCollectionUnavailable(
            "Plex would not answer this smart filter: %s" % type(error).__name__
        ) from None
    return len(items)


def require_matches(section, url: str) -> int:
    """Kometa's ``test_smart_filter`` (modules/plex.py:1580-1584), C8's half.

    Returns how many items the filter matches right now, and refuses at zero.
    The count is not stored anywhere -- a smart collection's membership is
    Plex's and changes without us -- it exists only so the action string can say
    what the operator's filter actually found.
    """
    matched = count_matches(section, url)
    if not matched:
        raise SmartFilterMatchedNothing(
            "this search matches nothing in this library right now, and a smart "
            "collection built from it would be permanently empty. Widen the "
            "filter, or narrow the definition with `libraries:` so it only "
            "targets libraries that can answer it. (Kometa refuses here too -- "
            "modules/plex.py:1580-1584 -- behind an `ignore_blank_results` "
            "switch this service deliberately does not offer.)"
        )
    return matched
```

- [ ] **Step 4: Add `minimum_items` to `DynamicParams`**

After `max_collections` (`dynamic.py:260`):

```python
    # Roadmap decomposition step 4's other half, and a deliberate DIVERGENCE:
    # upstream has no per-key minimum for any of the thirteen library dynamic
    # types (the nearest thing is the people types' `data: minimum`, which is a
    # credit threshold on the ENUMERATION and not on the result). So the default
    # is None and not a number -- a shipped floor an operator has to discover
    # and switch off is the surprise this project refuses -- and switching it on
    # costs one Plex read per key per pass, which is why it is opt-in twice
    # over: by existing, and by being priced in its own docstring.
    minimum_items: int | None = Field(default=None, ge=1)
```

- [ ] **Step 5: Add the below-minimum branch to `apply`**

Import `count_matches` alongside the existing `smart` imports
(`dynamic.py:99-103`). Inside the per-unit loop, between the successful
`build_search_url` call and `reconcile_smart_collection` (`dynamic.py:607-615`),
insert:

```python
                if params.minimum_items is not None:
                    matched = count_matches(ctx.section, url)
                    if matched < params.minimum_items:
                        # Out of the pass's record, which is the WHOLE of
                        # delete-below-minimum: this key is not one the family
                        # builds this pass, so an existing collection under this
                        # title is an ordinary sweep candidate and goes through
                        # the same guards every other one does. Nothing is
                        # deleted here.
                        generated.discard(unit.title)
                        actions.append(
                            "did not build %r: it matches %d item(s) and "
                            "`minimum_items` is %d. If a collection already "
                            "exists under that title, the delete sweep decides "
                            "its fate through `delete_unconfigured` and "
                            "`max_deletes` like any other"
                            % (unit.title, matched, params.minimum_items)
                        )
                        continue
```

It sits inside the existing `try:` so a `SmartCollectionUnavailable` from the
count is contained to this one key by the `except REFUSALS` below — the same
containment every other per-key failure gets.

- [ ] **Step 6: Add the two report additions**

Both go in `apply`, after `titled` is computed and before the emission loop.
The absent-value report (which also closes T4's deferred "silent None-key
drop"):

```python
        # ``family_titles`` drops an ``ABSENT_KEY`` key outright and is right
        # to: it is Plex's "these items have no value for this field", not a
        # value, and a collection called "Top None movies" is nobody's ask. But
        # dropping it silently leaves an operator whose library has forty
        # unrated films with a family that has no bucket for them and no reason
        # why. Reported, and honestly: at this layer a real tag named literally
        # "None" is the same string, so the report says so instead of claiming
        # to know which one this was.
        if any(key == ABSENT_KEY for key, _ in enumerated):
            actions.append(
                "%r reports a %r value of %r, which is how Plex spells 'these "
                "items have no value for this field'. No collection is built "
                "for it -- the query would be `%s=None`, which is a real search "
                "and a useless collection. If this library genuinely has a %s "
                "tag named %r, the two are the same string here and there is no "
                "way to tell them apart: rename the tag in Plex"
                % (ctx.library, params.type, ABSENT_KEY, row.search_key,
                   params.type, ABSENT_KEY)
            )
```

The inert-entry report (Minor N-5). Put the helper next to `_refused`:

```python
    def _inert(self, params: DynamicParams, present: set[str]) -> str | None:
        """Narrowing and override entries naming a key the library never
        reported, as one line or none.

        Silently inert is upstream's behaviour and is not a correctness hole --
        an all-excluded family does refuse. It is a TYPO an operator cannot
        see: ``include: [Horor]`` builds a family with one fewer collection and
        says nothing at all. One line rather than one per entry, because a
        config ported from another library can name a dozen at once.
        """
        named: list[str] = []
        for where, keys in (
            ("include", params.include),
            ("exclude", params.exclude),
            ("addons", list(params.addons)),
            ("key_name_override", list(params.key_name_override)),
            ("title_override", list(params.title_override)),
        ):
            missing = [str(one) for one in keys if str(one) not in present]
            named += ["%s: %r" % (where, one) for one in missing]
        if not named:
            return None
        return (
            "%s names no value %r holds and does nothing: %s. Every entry is "
            "matched against the key exactly -- for a type keyed on `choice.key`"
            " that is the bare form (`1980`, not `1980s`) -- so a near miss is "
            "silently inert rather than an error"
            % ("One entry" if len(named) == 1 else "%d entries" % len(named),
               ctx.library, ", ".join(named))
        )
```

`_inert` needs `ctx` for the library name, so make it take one:
`def _inert(self, ctx, params, present) -> str | None`, and call it as

```python
        present_keys = {key for key, _ in enumerated} | {
            value for _, value in enumerated
        }
        inert = self._inert(ctx, params, present_keys)
        if inert is not None:
            actions.append(inert)
```

`present_keys` is keys AND display values because `exclude` is matched against
both (`dynamic_keys.py:137`) — reporting a correct `exclude: [1930s]` as inert
would be worse than not reporting at all.

`actions` must therefore be declared before these two blocks; move
`actions: list[str] = []` (`dynamic.py:548`) up to just after the
`max_collections` check.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
docker compose -p p10a2t2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_smart.py tests/test_builder_dynamic.py tests/test_builder_smart_filter.py tests/test_smart_filter_engine.py 2>&1 | tee /app/.superpowers/run-t2-green1.log; echo EXIT=$?'
```

Expected: all pass. `test_builder_smart_filter.py` and
`test_smart_filter_engine.py` are in the run because they are `require_matches`'
other callers.

- [ ] **Step 8: The mutation proof**

- **Mutation A — the boundary.** Change `matched < params.minimum_items` to
  `matched <= params.minimum_items`. Expected RED:
  `test_a_key_at_the_minimum_is_created`.
- **Mutation B — the sweep coupling.** Delete the `generated.discard(unit.title)`
  line. Expected RED: `test_a_key_below_the_minimum_leaves_the_sweeps_record`.
  This is the mutation that proves delete-below-minimum is one mechanism with
  Task 1 rather than two that happen to agree.
- **Mutation C — the default's price.** Change `if params.minimum_items is not
  None:` to `if True:` with `params.minimum_items or 1`. Expected RED:
  `test_no_minimum_costs_no_extra_plex_read`.

Backup + cmp for each, real output both halves.

- [ ] **Step 9: Gate and commit**

```bash
docker compose -p p10a2t2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10a2t2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-t2-golden.log; echo EXIT=$?'
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
docker compose -p p10a2t2 down
```

```bash
git add src/autoposter/collections/smart.py \
        src/autoposter/collections/builders/dynamic.py \
        tests/test_collection_smart.py tests/test_builder_dynamic.py
git commit --no-gpg-sign -m "feat(dynamic): delete-below-minimum, and what the family reports

minimum_items is a per-key match count, opt-in and defaulting to None because
upstream has no per-key minimum for any library dynamic type. A key below it
leaves the pass's generated record and the family sweep decides its fate through
delete_unconfigured and max_deletes -- one mechanism, not a second delete path.
count_matches is split out of require_matches so the two questions cannot be
read as one. Also: the absent-value key and inert include/exclude/addons/override
entries are now reported instead of silently dropped (10a-1 review, T4 and N-5)."
```

---

## Task 3: The mandatory old-vs-new equivalence proof

> **CORRECTION (2026-08-27, filed by Task 6's wrap — the plan text below is
> left as written and is out of date in two counts).**
>
> 1. **"The four premises" is five.** Task 3's fix round shipped a fifth,
>    `P5` — an unencoded `+` in a value reaches the server's matcher as a
>    SPACE — and it is live-measured rather than reasoned, at P2's standard.
>    It was needed because the proof turned up a real defect rather than
>    confirming a swap: `build_search_url`'s tag branch emitted a resolved key
>    raw, and `content_rating` is the one tag family whose Plex key IS its
>    title. `member_sets.py` says "Five premises" and is the authority; the
>    three "four premises" lines below (this task's table, its `member_sets.py`
>    docstring draft, and Task 6's quoted row-185 text) are the plan as it was
>    believed when the tasks were dispatched. The row as SHIPPED says five.
> 2. **The encoding-fix micro-task's "four strict xfails" is three markers.**
>    Task 3 left three `xfail(strict=True)` tests spanning seventeen
>    parametrized cases, plus ONE deliberately-unmarked test that measured the
>    broken shape and was written to go red on the same fix. Four things
>    retired themselves; three of them carried the strict marker. The count
>    matters only because "four markers" would have left a reviewer looking
>    for a marker that was never there.

**Files:**
- Create: `tests/oracle/10a2/README.md`
- Create: `tests/oracle/10a2/plexapi_side.py`, `tests/oracle/10a2/ours_side.py`,
  `tests/oracle/10a2/member_sets.py`

**How the drivers are loaded — settled, not left open.** `tests/oracle/` has no
`__init__.py` anywhere and `tests/oracle/9b/` is not a package:
`tests/test_collection_search_oracle.py:104` holds a `Path` to the driver and
`:276` loads it with `importlib.util.spec_from_file_location`. Follow that
idiom exactly — it is also what makes the directory name `10a2` legal, since
`import tests.oracle.10a2` would be a `SyntaxError`. **Do not add an
`__init__.py`, and do not import the drivers by name.** The gate's header is
therefore:

```python
import importlib.util
from pathlib import Path

ORACLE = Path(__file__).parent / "oracle" / "10a2"


def _driver(name):
    """Load one oracle driver by PATH, the way 9b's gate loads its own
    (tests/test_collection_search_oracle.py:276). ``tests/oracle`` is not a
    package -- deliberately, since a module name starting with a digit cannot
    be imported -- so a path load is the only way in, and it is the established
    one."""
    spec = importlib.util.spec_from_file_location(
        "cs_equivalence_" + name, ORACLE / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


member_sets = _driver("member_sets")
ours_side = _driver("ours_side")
plexapi_side = _driver("plexapi_side")
```
- Create: `tests/test_collection_cs_equivalence.py`
- Read-only: `src/autoposter/assets/collections/content_rating_cs.json`

**Interfaces:**
- Produces:
  - `member_sets.decode(query: str, libtype: str) -> Group` — a query string
    from `?` onward, decoded into a predicate tree.
  - `member_sets.members(query: str, library: list[dict], libtype: str) ->
    set[str]` — the rating keys the query selects.
  - `plexapi_side.old_query(libtype: str, values, ratings, field_key: str) ->
    str` — what plexapi's own `_buildSearchKey` produces for
    `filters={"contentRating": values}` with `sort="originallyAvailableAt:desc"`.
  - `ours_side.new_query(libtype: str, values, present: set[str]) -> str` — what
    `parse_filters(..., base="any")` + `build_search_url` produces.
  - `ours_side.MOVIE_RATINGS` / `ours_side.SHOW_RATINGS` — the two vocabularies
    the proof runs against, chosen so every bucket shape is reachable.

  The bucket shapes themselves are NOT a table in either driver: the gate walks
  `buckets.derive_buckets` over the shipped `content_rating_cs.json`, so a table
  edit cannot leave a proof about the old table standing.

**Why this task exists and why it is mandatory.** Addendum 2 point 2. The golden
gate records `updated_filters` — a plexapi CALL SHAPE — so once Task 4 stops
calling plexapi the gate stops covering that cell, and a gate that no longer
covers the thing being changed is not the instrument for changing it. What has
to be proven is at a level the fixture cannot reach: that the OLD query and the
NEW query select the same items.

**The four premises**, each named in `member_sets.py` with its citation, so a
reviewer can reject a premise rather than having to re-derive the whole thing:

| | Premise | Evidence |
| --- | --- | --- |
| P1 | `field=a,b` — a comma-joined multi-value tag — is OR over the values | plexapi builds exactly that for a multi-value tag filter (`library.py:1102`, `','.join(result)`); the shipped Common Sense family is built that way and its live membership was verified against production for Movies bucket 17, Shows bucket 14 and the Shows catch-all (`buckets.py:9-11`) |
| P2 | `push=1 … pop=1` groups, `or=1` between adjacent terms is OR, `and=1` is AND | 9b's live read-only probe: the same five Spanish subtitle variants return **442** under `any:` and **0** under `all:` (`docs/research/plex-search-probe/README.md` §4) |
| P3 | `sort=` and `limit=` do not affect which items match, only which are returned and in what order | Kometa's own assembly order (`builder.py:4287-4289`); `limit` is `None` on both sides of this comparison anyway |
| P4 | in a query already scoped by `type=N`, a bare `contentRating` and an `<libtype>.contentRating` name the same field | 9b's oracle configs `12` and `17-country-on-a-show` pin the dotted spelling as what Kometa emits for a show library, and 9b's live probe searched it successfully |

P4 is the one that is a real question rather than a formality, and the driver
MEASURES rather than assumes it: `plexapi_side.py` runs plexapi's actual
`_validateFilterField`, so whichever spelling plexapi produces for each libtype
is the one the proof compares against.

- [ ] **Step 1: Write `member_sets.py`**

```python
"""Plex query strings as member sets: the instrument the golden gate cannot be.

``tests/fixtures/collections/golden_port.json`` records ``updated_filters`` --
the plexapi CALL SHAPE the Common Sense port removes -- so it cannot grade the
port. What has to be graded is whether the OLD query and the NEW one select the
same items, which is a question about SEMANTICS and not about bytes.

This module answers it by decoding both query strings into the same predicate
tree and evaluating the trees over a fixture library. Four premises, each cited,
so a reviewer can reject one rather than re-derive the module:

P1. ``field=a,b`` (a comma-joined multi-value tag) is OR over the values.
    plexapi constructs exactly that (library.py:1102, ``','.join(result)``) and
    the shipped Common Sense family is built that way -- its live membership was
    verified against production for Movies bucket 17, Shows bucket 14 and the
    Shows catch-all (collections/buckets.py:9-11).
P2. ``push=1 ... pop=1`` groups; ``or=1`` between adjacent terms is OR and
    ``and=1`` is AND. MEASURED: the same five Spanish subtitle variants return
    442 items under an ``any:`` base and 0 under ``all:``
    (docs/research/plex-search-probe/README.md, section 4).
P3. ``sort=`` and ``limit=`` decide what is returned and in what order, never
    what matches (builder.py:4287-4289). Both are dropped here. ``limit`` is
    ``None`` on both sides of this comparison in any case.
P4. In a query already scoped by ``type=N``, a bare ``contentRating`` and a
    ``<libtype>.contentRating`` name the same field. 9b's oracle configs pin the
    dotted spelling as Kometa's for a show library and 9b's live probe searched
    it. This module strips a leading ``<libtype>.`` from a field name and says
    so rather than treating the two spellings as different fields -- which would
    make the proof pass for the wrong reason (two queries that select nothing
    both select nothing).

Pure. No Plex, no plexapi, no repository imports: it is an oracle, and an oracle
that imported the thing it grades would grade nothing.
"""
from dataclasses import dataclass
from urllib.parse import unquote

# P3: parameters that decide what is RETURNED and in what order, never what
# matches. Dropped before the tree is built, so neither side's sort spelling
# (plexapi's ``movie.originallyAvailableAt:desc`` against ours,
# ``originallyAvailableAt%3Adesc``) can make two equivalent queries compare
# unequal or two different ones compare equal.
IGNORED = ("type", "sort", "limit", "includeGuids", "title")


@dataclass(frozen=True)
class Term:
    field: str
    values: tuple[str, ...]      # comma-joined values, already split (P1)


@dataclass(frozen=True)
class Group:
    op: str                      # "or" | "and"
    children: tuple


def _strip_libtype(field: str, libtype: str) -> str:
    """P4. ``show.contentRating`` and ``contentRating`` under ``type=2`` are one
    field. Only the query's OWN libtype prefix is stripped: a genuinely
    different scope (``episode.contentRating`` in a show query) is a different
    field and stays one."""
    prefix = libtype + "."
    return field[len(prefix):] if field.startswith(prefix) else field


def decode(query: str, libtype: str) -> Group:
    """One query string from ``?`` onward, as a predicate tree.

    ``push``/``pop`` open and close a group; ``or=1``/``and=1`` set the
    conjunction of the group they appear in; everything else is a term. A query
    with no explicit group is one implicit AND group, which is what an ``all:``
    base and plexapi's own flat parameter list both are.
    """
    stack: list[list] = [[]]
    ops: list[str] = ["and"]
    for pair in query.lstrip("?").split("&"):
        if not pair:
            continue
        name, _, raw = pair.partition("=")
        if name == "push":
            stack.append([])
            ops.append("and")
            continue
        if name == "pop":
            children = tuple(stack.pop())
            op = ops.pop()
            stack[-1].append(Group(op, children))
            continue
        if name in ("or", "and"):
            ops[-1] = name
            continue
        if name in IGNORED:      # P3
            continue
        stack[-1].append(Term(
            _strip_libtype(name, libtype),
            tuple(unquote(one) for one in unquote(raw).split(",")),   # P1
        ))
    return Group(ops[0], tuple(stack[0]))


def _matches(node, item: dict) -> bool:
    if isinstance(node, Term):
        held = item.get(node.field)
        return held is not None and held in node.values
    if not node.children:
        # No terms at all is THE WHOLE LIBRARY, which is the failure mode
        # ``SearchProducedNothing`` exists to refuse. Never silently True here:
        # a proof where both sides select everything proves nothing.
        raise ValueError("a query with no predicate matches the whole library")
    results = [_matches(child, item) for child in node.children]
    return any(results) if node.op == "or" else all(results)


def members(query: str, library: list[dict], libtype: str) -> set[str]:
    """The rating keys ``query`` selects out of ``library``.

    ``library`` is ``[{"ratingKey": "m1", "contentRating": "G"}, ...]`` -- one
    dict per item, whose keys are field names in the query's own vocabulary,
    undotted. ``libtype`` is the query's own scope and is used for exactly one
    thing: stripping its prefix off a field name (P4).
    """
    tree = decode(query, libtype)
    return {item["ratingKey"] for item in library if _matches(tree, item)}
```

- [ ] **Step 2: Write the two drivers and the shape table**

`tests/oracle/10a2/ours_side.py`:

```python
"""Our side of the equivalence: parse_filters + build_search_url.

Unlike ``member_sets.py`` this one DOES import from the repository -- that is
the point of it. Runnable on its own so a reviewer can put the two query
strings side by side without pytest::

    docker compose -p p10a2t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
        run --rm test python tests/oracle/10a2/ours_side.py

Loaded by PATH by the gate, never imported by name: ``tests/oracle`` is not a
package (9b's driver is loaded the same way, and a module name starting with a
digit could not be imported anyway).
"""
import json
from pathlib import Path

from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import build_search_url

TABLE = json.loads(
    Path("src/autoposter/assets/collections/content_rating_cs.json").read_text(
        encoding="utf-8"
    )
)

# The ratings each library type is given for the proof. Chosen so every bucket
# shape below is reachable: a bucket that is its own key alone, a bucket whose
# key is absent and whose addons are present, a bucket with several present
# addons, and a non-empty complement for the ``other`` bucket.
MOVIE_RATINGS = ("G", "PG", "PG-13", "R", "NC-17", "NR", "Unrated", "TV-MA")
SHOW_RATINGS = ("TV-Y", "TV-G", "TV-14", "TV-MA", "NR")

# content_rating is the one tag type whose Plex key and title are the same
# string on this server (dynamic_types.py's content_rating note, measured by the
# 10a-1 probe), so the resolver is the identity over the present set -- and
# saying that out loud is what stops it being mistaken for a shortcut.
def resolver(present):
    def resolve(attribute, value, /):
        return (str(value),) if str(value) in present else ()
    return resolve


def new_query(libtype: str, values, present) -> str:
    group = parse_filters(
        {"content_rating": list(values)},
        field="params", searching=True, base="any",
    )
    return build_search_url(
        group, libtype=libtype, sort_by=("release.desc",), limit=None,
        resolve_tag=resolver(present),
    )
```

`tests/oracle/10a2/plexapi_side.py` drives plexapi's REAL code:

```python
"""The OLD side: plexapi's own ``_buildSearchKey``, driven for real.

Not a transcription. ``LibrarySection._buildSearchKey`` ->
``_validateAdvancedSearch`` -> ``_validateFilterField`` -> ``_validateFieldValue``
-> ``_validateFieldValueTag`` is the exact path
``section.createCollection(smart=True, filters=...)`` takes today
(plexapi/library.py:1259-1292, :1071-1103, :1128-1181), and running it is the
only way to learn what it emits per library type -- including whether the field
name comes back bare or ``<libtype>.``-prefixed, which premise P4 in
``member_sets.py`` is about and which this driver MEASURES rather than assumes.

The section is a real ``LibrarySection`` with its filter metadata stubbed: the
three lookups the path makes (``listFields``, ``getFieldType``, ``listOperators``
and ``listFilterChoices``) are answered from the fixture below, and nothing
reaches a network.
"""
from types import SimpleNamespace

from plexapi.library import LibrarySection


class _Section(LibrarySection):
    """``LibrarySection`` with the four filter-metadata lookups stubbed.

    Subclassed rather than faked so ``_buildSearchKey`` and everything under it
    is plexapi's real code -- a hand-written stand-in would be a transcription,
    and a transcription is what this whole task exists to avoid.
    """

    def __init__(self, key, libtype, field_key, ratings):
        self.key = key
        self.TYPE = libtype
        self._field_key = field_key
        self._ratings = list(ratings)

    def listFields(self, libtype=None):
        return [SimpleNamespace(key=self._field_key, type="tag")]

    def getFieldType(self, fieldType):
        return SimpleNamespace(
            type="tag", operators=[SimpleNamespace(key="=")],
        )

    def listOperators(self, fieldType):
        return self.getFieldType(fieldType).operators

    def listFilterChoices(self, field, libtype=None):
        return [SimpleNamespace(key=one, title=one) for one in self._ratings]

    def listSorts(self, libtype=None):
        return [SimpleNamespace(key="originallyAvailableAt")]


def old_query(libtype: str, values, ratings, field_key: str) -> str:
    """The query string plexapi builds for
    ``createCollection(smart=True, filters={"contentRating": values})``.

    Returned from ``?`` onward, to match ``build_search_url``'s shape.
    """
    section = _Section("42", libtype, field_key, ratings)
    key = section._buildSearchKey(
        sort="originallyAvailableAt:desc", libtype=libtype,
        filters={"contentRating": list(values)},
    )
    return "?" + key.partition("?")[2]
```

**`field_key` is a parameter, deliberately.** Whether a Plex show section
answers `/library/sections/N/filters?type=2` with a bare `contentRating` or a
dotted `show.contentRating` is a fact about the SERVER, and this driver must
not decide it. The test below runs BOTH spellings for the show case and asserts
the member sets agree either way — so the proof holds whichever one the real
server returns, and P4 is exercised rather than assumed.

- [ ] **Step 3: Write the gate**

`tests/test_collection_cs_equivalence.py`:

```python
"""The mandatory equivalence proof for the Common Sense grammar unification.

`.superpowers/sdd/p10a-facts.md` Addendum 2, point 2: because the golden gate
records ``updated_filters`` -- the plexapi CALL SHAPE the port removes -- it
stops covering the changed cell, so the proof that the port does not move a
single item is this file and not that fixture.

Every bucket shape in the SHIPPED table, against two library types: a bucket
that is its own key alone, one whose key the library does not hold and whose
addons it does, one with several present addons, and the ``other`` bucket's
complement. For each: plexapi's real query and this engine's real query, decoded
and evaluated to member sets over a fixture library, asserted equal.
"""
import json
from pathlib import Path

import pytest

from autoposter.collections.buckets import derive_buckets

# member_sets / ours_side / plexapi_side are loaded by PATH -- see the
# ``_driver`` helper above, and 9b's gate for the precedent.

TABLE = json.loads(
    Path("src/autoposter/assets/collections/content_rating_cs.json").read_text(
        encoding="utf-8"
    )
)


CONTROL = "ZZ-NOT-A-RATING"


def _library(ratings):
    """One item per rating, plus one carrying a rating no bucket claims -- the
    negative control. Without it, a decoder whose every predicate answered True
    would make both sides select everything and the comparison would pass."""
    items = [
        {"ratingKey": rating, "contentRating": rating} for rating in ratings
    ]
    items.append({"ratingKey": "control", "contentRating": CONTROL})
    return items


CASES = [
    ("movie", ours_side.MOVIE_RATINGS, "Movie", "contentRating"),
    ("show", ours_side.SHOW_RATINGS, "Show", "contentRating"),
    ("show", ours_side.SHOW_RATINGS, "Show", "show.contentRating"),
]


@pytest.mark.parametrize("libtype,ratings,library_type,field_key", CASES)
def test_every_bucket_selects_the_same_items_through_both_grammars(
    libtype, ratings, library_type, field_key
):
    present = set(ratings)
    library = _library(ratings)
    covered = {"key_only": 0, "addons": 0, "other": 0}

    for bucket in derive_buckets(present, library_type):
        if not bucket.values:
            # Addendum 3: an empty bucket is never created by either grammar,
            # here or in production (reconcile.py's `if not bucket.values`).
            continue
        old = plexapi_side.old_query(libtype, bucket.values, ratings, field_key)
        new = ours_side.new_query(libtype, bucket.values, present)
        old_members = member_sets.members(old, library, libtype)
        new_members = member_sets.members(new, library, libtype)

        # The absolute claim first: what the OLD query selects is exactly the
        # bucket's own ratings and nothing else. Without this, two decoders that
        # are both wrong in the same direction would agree below and the whole
        # comparison would prove nothing.
        assert old_members == set(bucket.values), (bucket.key, old)
        assert "control" not in old_members, (bucket.key, old)
        # Then the relative one, which is what Addendum 2 point 2 asks for.
        assert old_members == new_members, (bucket.key, old, new)

        if bucket.key == "other":
            covered["other"] += 1
        elif len(bucket.values) > 1:
            covered["addons"] += 1
        else:
            covered["key_only"] += 1

    # Every shape Addendum 2 names was actually exercised, not merely available.
    assert covered["key_only"], covered
    assert covered["addons"], covered
    assert covered["other"] == 1, covered


@pytest.mark.parametrize("libtype,ratings,library_type,field_key", CASES)
def test_the_two_grammars_disagree_on_bytes_and_that_is_the_point(
    libtype, ratings, library_type, field_key
):
    """The proof is worthless if the two sides are the same string: it would be
    proving that a query equals itself. Pin the difference so a future change
    that accidentally made them identical would have to say so."""
    present = set(ratings)
    multi = next(
        b for b in derive_buckets(present, library_type) if len(b.values) > 1
    )
    old = plexapi_side.old_query(libtype, multi.values, ratings, field_key)
    new = ours_side.new_query(libtype, multi.values, present)

    assert old != new
    assert "," in old and "or=1" not in old, old
    assert "or=1" in new and "push=1" in new and "," not in new, new


def test_the_shipped_table_is_what_was_proven():
    """The proof is over the SHIPPED table, so a table edit that added a bucket
    would otherwise leave a proof about the old one standing. Pinned by count
    rather than by content -- the content is the table's own test's business."""
    assert len(TABLE["include"]) == len(TABLE["addons"])
    assert TABLE["include"], "an empty table would make every assertion vacuous"
```

**The two drivers reference each other's data through the gate, not through
each other.** `ours_side.py` and `plexapi_side.py` must not import one another
(a path-loaded module cannot be imported by name), so `MOVIE_RATINGS` /
`SHOW_RATINGS` live in `ours_side.py` and the gate passes them into
`plexapi_side.old_query`. That is why `old_query` takes `ratings` as an
argument.

- [ ] **Step 4: Run the gate to verify it fails, then passes**

RED first — write `member_sets.py` LAST of the three, so the first run fails on
the import rather than on an assertion, and you see the test fail for the right
reason:

```bash
docker compose -p p10a2t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_cs_equivalence.py 2>&1 | tee /app/.superpowers/run-t3-red1.log; echo EXIT=$?'
docker compose -p p10a2t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_cs_equivalence.py 2>&1 | tee /app/.superpowers/run-t3-green1.log; echo EXIT=$?'
```

- [ ] **Step 5: Print both sides side by side and record them**

```bash
docker compose -p p10a2t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'python - <<PY 2>&1 | tee /app/.superpowers/run-t3-sides.log
import importlib.util
from pathlib import Path
from autoposter.collections.buckets import derive_buckets

def driver(name):
    spec = importlib.util.spec_from_file_location(
        name, Path("tests/oracle/10a2") / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ours_side, plexapi_side = driver("ours_side"), driver("plexapi_side")
present = set(ours_side.MOVIE_RATINGS)
for bucket in derive_buckets(present, "Movie"):
    if not bucket.values:
        continue
    print(bucket.key)
    print("  old", plexapi_side.old_query("movie", bucket.values, ours_side.MOVIE_RATINGS, "contentRating"))
    print("  new", ours_side.new_query("movie", bucket.values, present))
PY'
```

Paste the whole output into `tests/oracle/10a2/README.md` and into the task
report. This is the artefact a reviewer reads to check the proof rather than to
trust it, and it is what the PR body's migration paragraph is written from.
**Scrub check:** the fake server key is `42` and there is no host or token
anywhere in either string — grep the file for `http`, `token` and `X-Plex`
before committing it (global constraint 2).

- [ ] **Step 6: The mutation proof**

- **Mutation A — P1.** In `member_sets.decode`, stop splitting on commas
  (`tuple([unquote(raw)])`). Expected RED: every addons bucket, because the old
  side's `G,PG` becomes one literal value that no item holds.
- **Mutation B — P2.** In `_matches`, make `Group` always `all()`. Expected
  RED: every addons bucket on the NEW side, for the mirror-image reason — this
  is the 442-vs-0 fact from the 9b probe, reproduced in the evaluator.
- **Mutation C — the negative control.** Change `_matches`'s `Term` branch to
  `return True`. Expected RED: `assert old_members == set(bucket.values)` on
  every case, because both sides now select the whole fixture library including
  the control item — *while* `assert old_members == new_members` would still
  have passed. Record that asymmetry explicitly in the report: it is the whole
  reason the absolute assertion sits above the relative one, and a proof that
  cannot fail on an always-true predicate is not a proof.

- [ ] **Step 7: Gate and commit**

```bash
docker compose -p p10a2t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10a2t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-t3-golden.log; echo EXIT=$?'
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
docker compose -p p10a2t3 down
```

```bash
git add tests/oracle/10a2 tests/test_collection_cs_equivalence.py
git commit --no-gpg-sign -m "test(collections): the Common Sense grammar equivalence proof

Addendum 2 point 2: the golden fixture records plexapi's call shape, so it
cannot grade a port that stops calling plexapi. This proves what the fixture
cannot -- that both grammars select the same items -- by running plexapi's real
_buildSearchKey and this engine's real build_search_url for every shape in the
shipped table, decoding both query strings into predicate trees and evaluating
them to member sets over a fixture library. Four premises, each cited; P2 is the
9b probe's measured 442-under-any versus 0-under-all. Both library types, both
possible show field spellings, addons and the other-bucket complement, with a
negative control so an always-true predicate cannot pass."
```

---

## Task 4: The Common Sense write path, unified — and the one adjudicated cell

**Files:**
- Modify: `src/autoposter/collections/reconcile.py:49-67` (`definition_hash`),
  `:552-694` (`reconcile_content_ratings`)
- Modify: `src/autoposter/collections/builders/cs_bucket.py:70-86`
- Modify: `tests/test_builder_port_golden.py` (harness only)
- Modify: `tests/fixtures/collections/golden_port.json` — **its own commit**
- Modify: `tests/test_collection_reconcile.py`, `tests/test_collection_adoption.py`,
  `tests/test_collection_poster_wiring.py`, `tests/test_collection_protection.py`,
  `tests/test_collection_separator.py` (fake harnesses only)

**Interfaces:**
- Consumes: `smart.create_smart_collection(section, libtype, title, url)`,
  `smart.update_smart_collection(section, collection, url)`,
  `smart.smart_definition_hash(url, summary, settings)`,
  `search_url.build_search_url`, `filters.parse_filters`,
  `plex_search.LibraryTagResolver`.
- Produces: `reconcile_content_ratings(..., resolver=None)` — one new
  keyword-only argument, the pass-scoped `LibraryTagResolver`. `None` builds one
  from the section, the way `reconcile_smart_collection`'s `existing=None`
  falls back to listing.

**THE SHAPE OF THIS PORT, and why it is not a bigger one.** Addendum 2 point 1
scopes the fixture change to **one documented cell**. Routing the family through
`reconcile_smart_collection` instead would change its action strings ("created
'Age 1+ Movies'" → "created 'Age 1+ Movies' as a smart collection (N item(s)
match now)"), lose its per-bucket poster kinds (`content_rating` /
`content_rating_other` — `reconcile_smart_collection` passes `None`, `None`),
lose its derived per-bucket summaries and add a `require_matches` read per
bucket. That is dozens of changed cells and a behaviour change to 305 live
collections, which is what the golden gate exists to prevent. So the port is
**the write calls and the hash, and nothing else**: `reconcile_content_ratings`
keeps its reconciler, its ownership rules, its summaries, its posters, its
separator and every one of its action strings.

**The hash MUST change, and that is the migration.** If `definition_hash` kept
hashing `bucket.values`, the first pass after deploy would find every hash
current, skip every bucket, and leave every collection's stored filter in the
OLD grammar forever — the port would be a no-op in production while passing
every test. Folding the built URL into the hash is what makes the migration
happen: one `update_smart_collection` PUT per Common Sense collection on the
first pass, membership unchanged (that is Task 3's proof), and nothing after.

- [ ] **Step 1: Write the failing tests**

In `tests/test_collection_reconcile.py`:

```python
async def test_a_bucket_is_created_with_a_raw_post_and_no_plexapi_filters(session):
    """Roadmap row 185. The second query grammar is retired: this family now
    writes the same oracle-proven URI ``smart_filter`` writes, through the same
    two functions, so there is exactly one smart write path in this service."""
    section = FakeSection(["G", "PG"])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert "created 'Age 1+ Movies'" in actions, actions
    assert section.created == [], "plexapi createCollection must not be called"
    posted = [one for one in section.queries if one["method"] == "POST-SENTINEL"]
    assert posted, section.queries
    uri = parse_qs(urlsplit(posted[0]["key"]).query)["uri"][0]
    assert uri.startswith("server://")
    assert "push=1" in uri and "or=1" in uri and "contentRating=G" in uri


async def test_a_bucket_whose_filter_changed_is_updated_with_a_put(session):
    """The migration's own shape: an existing collection whose stored hash
    predates the port is not current, so the pass re-PUTs its filter. One PUT
    per collection, once."""
    existing = FakeCollection("Age 1+ Movies", labels=[LABEL])
    section = FakeSection(["G"], existing=[existing])
    session.add(ManagedCollection(
        library="Movies", title="Age 1+ Movies", kind="smart",
        plex_rating_key="1", definition_hash="the-pre-port-hash",
    ))
    await session.flush()

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert "updated 'Age 1+ Movies'" in actions, actions
    assert existing.updated_filters is None, (
        "plexapi updateFilters must not be called"
    )
    put = [one for one in section.queries if one["method"] == "PUT-SENTINEL"]
    assert len(put) == 1, section.queries
    assert "/items?" in put[0]["key"] and "uri=" in put[0]["key"]


async def test_the_hash_folds_the_built_uri_so_the_migration_actually_happens(
    session,
):
    """The one thing that could make this port a silent no-op in production: a
    hash over the bucket's VALUES is unchanged by the port, so the first pass
    would skip every collection and leave every stored filter in the old
    grammar. The hash is over the URI, so it cannot."""
    bucket = Bucket(key="1", title="Age 1+ Movies", summary="s", values=("G",))
    assert definition_hash(bucket, None, "?type=1&push=1&contentRating=G&pop=1") != (
        definition_hash(bucket, None, "?type=1&push=1&contentRating=PG&pop=1")
    )


async def test_an_unchanged_second_pass_still_writes_nothing(session):
    """The migration is ONE pass. The second finds the new hash stored and
    short-circuits exactly as it did before."""
    section = FakeSection(["G", "PG"])
    await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )
    before = len(section.queries)

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert actions == []
    assert len(section.queries) == before


async def test_an_empty_bucket_is_still_never_created_and_never_deleted(session):
    """Addendum 3, pinned at the port. Kometa DROPS a bucket no library value
    matches (meta.py:1224-1225); ``derive_buckets`` returns it with empty values
    and this reconciler declines to create or modify it, so production never
    creates one and never deletes one that already exists. The port does not
    change that, and ``cs_bucket.titles()`` still names every bucket -- which is
    what keeps an existing-but-now-empty Common Sense collection out of the
    delete sweep's candidate set entirely."""
    from autoposter.collections.builders.cs_bucket import CsBucketBuilder

    stale = FakeCollection("Age 18+ Movies", labels=[LABEL])
    section = FakeSection(["G"], existing=[stale])

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert not any("Age 18+ Movies" in one for one in actions), actions
    assert "Age 18+ Movies" in CsBucketBuilder().titles("Movie", _config())


async def test_a_bucket_whose_query_cannot_be_built_refuses_only_itself(session):
    """``build_search_url`` and ``parse_filters`` can refuse, and this
    reconciler is reached through a SMART builder whose ``apply`` the engine
    does not wrap -- so an uncaught refusal here costs the whole library its
    reconcile. Contained to one bucket, like every other per-key refusal in this
    service."""
    section = FakeSection(["G", "PG"], resolves={"G"})   # PG resolves to nothing

    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
    )

    assert any("created 'Age 1+ Movies'" in one for one in actions), actions
    assert any("refused" in one and "PG" in one for one in actions), actions
```

In `tests/test_builder_port_golden.py`, one new assertion beside the gate:

```python
async def test_the_common_sense_family_writes_no_plexapi_filters_at_all(
    session, config_factory, tmp_path
):
    """The one cell the fixture's amendment is about, asserted directly rather
    than only through the recorded state: after the port no collection in any
    scenario is created or updated through plexapi's ``filters=``, so every
    ``filters`` cell in the fixture is a POSTed uri or None and never a dict."""
    recorded = await _scenarios(session, config_factory, tmp_path)
    for name, scenario in recorded.items():
        for title, state in scenario["state"].items():
            assert not isinstance(state["filters"], dict), (name, title)
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p p10a2t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_reconcile.py 2>&1 | tee /app/.superpowers/run-t4-red1.log; echo EXIT=$?'
```

Expected: `TypeError: definition_hash() takes 2 positional arguments but 3 were
given`, `AttributeError: 'FakeSection' object has no attribute 'queries'`, and
`assert section.created == []` failing with the plexapi call recorded.

- [ ] **Step 3: Amend the CS reconcile-test harness**

`tests/test_collection_reconcile.py`'s `FakeSection` needs what a raw POST/PUT
and a tag resolution need. The other four direct-caller test files
(`test_collection_adoption.py`, `test_collection_poster_wiring.py`,
`test_collection_protection.py`, `test_collection_separator.py`) each carry
their own copy of these fakes and need the same three additions — make the same
edit in each, and say in the task report that you did.

```python
class FakeChoice:
    def __init__(self, title, key=None):
        self.title = title
        # Plex answers this filter's key and title with the same string -- the
        # 10a-1 probe measured it (dynamic_types.py's content_rating note) --
        # so the resolver is the identity here, which is also what the
        # equivalence proof's driver assumes and says out loud.
        self.key = title if key is None else key


class FakeSection:
    def __init__(self, ratings, existing=(), resolves=None, section_type="movie"):
        self._ratings = list(ratings)
        self._existing = {c.title: c for c in existing}
        self._resolves = set(ratings) if resolves is None else set(resolves)
        self.created = []
        self.queries = []
        self.key = "42"
        self.type = section_type
        self._server = self
        self._session = type("Sess", (), {
            "post": "POST-SENTINEL", "put": "PUT-SENTINEL",
        })()

    def _uriRoot(self):
        return "server://FAKE-MACHINE-ID/com.plexapp.plugins.library"

    def query(self, key, method=None, **kwargs):
        """Both raw routes: the create POST (which carries a ``title``) and the
        filter-replacing PUT (which carries only a ``uri``)."""
        self.queries.append({"key": key, "method": method})
        args = parse_qs(urlsplit(key).query)
        if "title" in args:
            title = args["title"][0]
            self._existing[title] = FakeCollection(
                title, labels=[], rating_key=str(len(self._existing) + 1),
            )
        return None

    def collection(self, title):
        return self._existing[title]

    def listFilterChoices(self, field, libtype=None):
        assert field == "contentRating"
        return [
            FakeChoice(r) for r in self._ratings
            if r in self._resolves or libtype is None
        ]
```

`resolves` exists for one test only —
`test_a_bucket_whose_query_cannot_be_built_refuses_only_itself` — and it is how
that test makes a value unresolvable without inventing a Plex failure. Keep the
listing (`present`) complete and narrow only the resolver's view, which is what
`_resolves` does when `libtype` is passed (the resolver always passes one; the
direct `listFilterChoices("contentRating")` call does not).

- [ ] **Step 4: Change `definition_hash` to fold the URI**

`src/autoposter/collections/reconcile.py:49-67`:

```python
def definition_hash(bucket: Bucket, settings=None, url: str = "") -> str:
    """Hash the desired filter, summary, and ride-along settings.

    ``url`` is the BUILT query string this bucket's collection stores in Plex,
    and it is in the payload for the reason phase 10a-2 exists: the family's
    write path moved from plexapi's ``filters=`` grammar onto the raw-POST 9b
    one, and a hash over the bucket's VALUES alone would be unchanged by that
    move -- so the first pass after the migration would find every hash current,
    skip every bucket, and leave every collection's stored filter in the retired
    grammar forever. Folding the URI is what makes the one-time re-PUT happen,
    and what makes it happen exactly once. It defaults to empty so the shape of
    a bucket's hash without one is still computable, which the separator's
    constant and this module's own tests rely on.

    ``settings`` folds in the same way ``lists._settings_parts`` folds it into
    the list-collection members hash, and for the same reason: a pass
    short-circuits on this hash, so a definition whose only edit was a new
    label or sort title would otherwise be recognised as already current and
    the edit would never be applied. Imported locally -- ``lists.py`` imports
    from this module at load time, so a module-level import here would be a
    cycle.
    """
    from autoposter.collections.lists import _settings_parts

    payload = "\x1f".join([
        bucket.title, bucket.summary, *bucket.values, url,
        *_settings_parts(settings),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Port `reconcile_content_ratings`**

Add the imports at the top of `reconcile.py` — but note the CYCLE:
`smart.py` imports from `reconcile.py` at module load (`smart.py:52-58`), so
the three `smart` names must be imported **inside the function**, exactly as
`definition_hash` imports `_settings_parts`. `parse_filters`, `build_search_url`
and their exception classes have no cycle and go at module level:

```python
from autoposter.collections.builders.plex_search import (
    LibraryTagResolver,
    PlexSearchUnavailable,
)
from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import (
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    TagValueNotFound,
    build_search_url,
)
```

If `plex_search` turns out to import `reconcile` too, import
`LibraryTagResolver` inside the function alongside the `smart` names and say so
in a comment. **Check this before writing the import** — `python -c "import
autoposter.collections.reconcile"` inside the container is the check.

Add the parameter to the signature (after `settings=None`):

```python
    resolver=None,
```

Add to the docstring:

```
    ``resolver`` is the pass's ``LibraryTagResolver``, which answers both halves
    of this reconcile: what content ratings the library holds, and which Plex
    key each written rating resolves to. Passing the pass's own instance is what
    keeps the whole family to ONE ``listFilterChoices`` round trip, memoised
    beside every other builder's. Omitting it builds one from ``section`` here,
    which is the same class with a private cache -- the fallback
    ``reconcile_smart_collection``'s ``existing=None`` already has, and the
    right answer for a direct caller with no pass around it.
```

Replace the body's opening (`reconcile.py:593-595`):

```python
    from autoposter.collections.smart import (
        create_smart_collection,
        update_smart_collection,
    )

    libtype = LIBTYPES[library_type]
    if resolver is None:
        # A pass-less caller. ``LibraryTagResolver`` needs only ``library`` and
        # ``run_cache`` off its context, so a private one is a complete context
        # for a single reconcile -- it simply memoises nothing beyond this call.
        resolver = LibraryTagResolver(
            SimpleNamespace(library=library_name, run_cache={}), section, libtype,
        )
    try:
        present = {title for _, title in resolver.choices("content_rating")}
    except PlexSearchUnavailable as refusal:
        # The whole family's input. Returned rather than raised: this reconciler
        # is reached through a SMART builder, and ``engine.py``'s smart dispatch
        # does not wrap ``apply`` -- an escape here would cost the library its
        # entire reconcile because one listing call failed.
        logger.warning("%s: the Common Sense family was not built: %s",
                       library_name, refusal)
        return ["refused the Common Sense collections: %s" % refusal]
    existing = {collection.title: collection for collection in section.collections()}
```

(`SimpleNamespace` from `types` at module level.)

Then replace the write block (`reconcile.py:639-659`). The `wanted` computation
moves down, because it now needs the URL:

```python
    for bucket in derive_buckets(present, library_type):
        if not bucket.values:
            # An empty filter matches the entire library. Never create one,
            # and leave any existing collection exactly as it is. Addendum 3:
            # Kometa DROPS such a bucket at derive time and we keep it, so this
            # branch is the difference -- and ``cs_bucket.titles()`` still names
            # it, which is what keeps an existing-but-now-empty collection out
            # of the delete sweep's candidate set.
            continue

        collection = existing.get(bucket.title)
        if collection is not None:
            ok, message = resolve_collision(
                collection, label, adopt, adopt_from or [], adopt_removes_prior_label, dry_run,
                protect_labels or [],
            )
            if message:
                actions.append(message)
            if not ok:
                continue

        try:
            # The 9b grammar, under an ``any:`` base -- one term per value
            # joined by ``or=1``, which is the reading this family has always
            # relied on and which the equivalence proof
            # (``tests/test_collection_cs_equivalence.py``) shows selects
            # exactly the items plexapi's comma-joined form did.
            url = build_search_url(
                parse_filters(
                    {"content_rating": list(bucket.values)},
                    field="params", searching=True, base="any",
                ),
                libtype=libtype,
                sort_by=("release.desc",),
                limit=None,
                resolve_tag=resolver,
            )
        except (
            ValueError, SearchAttributeNotAvailable, SearchProducedNothing,
            TagValueNotFound,
        ) as refusal:
            # Contained to ONE bucket, for the reason above: seventeen working
            # collections must not stop being managed because an eighteenth
            # holds a rating Plex will not resolve.
            logger.warning("%s: %r was not built: %s",
                           library_name, bucket.title, refusal)
            actions.append("refused %r: %s" % (bucket.title, refusal))
            continue

        wanted = definition_hash(bucket, settings, url)
        record = stored.get(bucket.title)
        definition_current = (
            collection is not None and record is not None and record.definition_hash == wanted
        )
        if definition_current and not (posters_on and record.poster_sha256 is None):
            continue

        if not definition_current:
            if dry_run:
                actions.append(
                    "%s %r -> %s"
                    % ("would update" if collection else "would create",
                       bucket.title, ", ".join(bucket.values))
                )
            else:
                if collection is None:
                    collection = create_smart_collection(
                        section, libtype, bucket.title, url
                    )
                    collection.addLabel(label)
                    actions.append("created %r" % bucket.title)
                else:
                    update_smart_collection(section, collection, url)
                    actions.append("updated %r" % bucket.title)

                _edit_collection_summary(collection, bucket.summary)
                actions += apply_collection_settings(
                    section, collection, settings, label, config
                )
                ...
```

Everything below (`record` creation, the poster block, the separator, the
`flush`) is **unchanged**. `SORT` (`reconcile.py:26`) becomes unused by this
function and is replaced by `sort_by=("release.desc",)`, which
`search_sorts.py:83` renders as `originallyAvailableAt%3Adesc` — the same sort
plexapi was asked for. Leave the `SORT` constant in place if anything else
reads it; if `grep -rn "reconcile.SORT\|import SORT" src tests` finds nothing
else, remove it and say so in the report (it is an orphan YOUR change created,
which global constraint 3 of the user's own guidelines says to clean up).

Note the ORDER change: `resolve_collision` now runs BEFORE the hash is
computed, because the hash needs the URL and building the URL costs a
resolution. That is a strict improvement — a protected or foreign collection is
now skipped without building its query at all — and it does not move an action
string, because `resolve_collision`'s message was already appended before the
skip decision.

- [ ] **Step 6: Pass the pass-scoped resolver from `cs_bucket`**

`src/autoposter/collections/builders/cs_bucket.py`:

```python
    async def apply(self, ctx: SmartContext) -> list[str]:
        return await reconcile_content_ratings(
            ctx.session,
            ctx.section,
            ctx.library,
            ctx.library_type,
            ctx.label,
            dry_run=ctx.dry_run,
            adopt=ctx.config.collections.adopt,
            adopt_from=ctx.config.collections.adopt_from,
            adopt_removes_prior_label=ctx.config.collections.adopt_removes_prior_label,
            separators=ctx.config.collections.separators,
            protect_labels=ctx.config.collections.protect_labels,
            http=ctx.http,
            config=ctx.config,
            settings=ctx.definition,
            # The pass's own resolver: one ``listFilterChoices`` for this
            # library, memoised in ``run_cache`` beside every other builder's,
            # and the same instance the dynamic engine enumerates through -- so
            # a written rating cannot resolve to two different Plex keys in two
            # builders in one pass.
            resolver=LibraryTagResolver(ctx, ctx.section, LIBTYPES[ctx.library_type]),
        )
```

with `from autoposter.collections.builders.plex_search import LibraryTagResolver`
and `LIBTYPES` added to the existing `reconcile` import. Update the module
docstring's "It is deliberately a thin wrapper. Nothing about the bucket
derivation, the ownership rules or the separator moved" sentence to add:

```
Phase 10a-2 moved exactly one thing through it: the pass's ``LibraryTagResolver``,
which is what lets the reconciler build its query in the 9b grammar (roadmap row
185) on ONE listing round trip shared with every other builder in the pass.
```

- [ ] **Step 7: Run the CS tests to GREEN, then the golden to RED**

```bash
docker compose -p p10a2t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_reconcile.py tests/test_collection_adoption.py tests/test_collection_poster_wiring.py tests/test_collection_protection.py tests/test_collection_separator.py tests/test_collection_cs_equivalence.py 2>&1 | tee /app/.superpowers/run-t4-green1.log; echo EXIT=$?'
docker compose -p p10a2t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-t4-goldenred.log; echo EXIT=$?'
```

The first must be GREEN. The second must be RED, and its failure must be
**exclusively** the `filters` cell of Common Sense collections — read the diff
in the log and confirm that before going on. If any action string, summary,
sort title, label, member list or poster count differs, **STOP and report**:
Addendum 2 licensed one cell, and anything else is a behaviour change to 305
live collections that nobody has approved.

- [ ] **Step 8: Commit the code, before the fixture**

Two commits, in this order, so a reviewer can see the gate break and then see
exactly what was licensed to fix it.

```bash
git add src/autoposter/collections/reconcile.py \
        src/autoposter/collections/builders/cs_bucket.py \
        tests/test_collection_reconcile.py tests/test_collection_adoption.py \
        tests/test_collection_poster_wiring.py tests/test_collection_protection.py \
        tests/test_collection_separator.py
git commit --no-gpg-sign -m "feat(collections): the Common Sense family writes the 9b grammar

Roadmap row 185. section.createCollection(smart=True, filters=...) and
Collection.updateFilters() are replaced by smart.create_smart_collection and
smart.update_smart_collection over a build_search_url query on an any: base --
the same oracle-proven URI every other smart collection this service manages is
written with. The second query grammar is retired.

Everything else about this family is untouched: its reconciler, its ownership
rules, its derived per-bucket summaries, its two poster kinds, its separator and
every one of its action strings. definition_hash now folds the built URI, which
is what makes the one-time migration happen and happen exactly once -- a hash
over the bucket's values alone would be unchanged by this and every collection
would keep its retired-grammar filter forever. A bucket whose query cannot be
built refuses alone rather than costing the library its reconcile.

Proven by tests/test_collection_cs_equivalence.py: both grammars select the same
items, for every bucket shape in the shipped table, on both library types."
```

- [ ] **Step 9: Amend the golden harness and re-capture the one cell**

**This is the step Addendum 2 point 1 governs. Read it before touching the
fixture.**

First the harness. `tests/test_builder_port_golden.py`'s `FakeSection.query`
handles only the POST route and `FakeChoice` has no key; both need to grow, and
the POST must RECORD the uri so the amended cell carries evidence rather than a
`None`:

```python
class FakeChoice:
    def __init__(self, title):
        self.title = title
        # Plex answers contentRating's key and title with the same string (the
        # 10a-1 dynamic probe measured it), so the resolver is the identity
        # here. Added when the Common Sense family started resolving its values
        # through LibraryTagResolver rather than handing plexapi the written
        # words.
        self.key = title
```

```python
    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """Both raw routes now.

        ``title`` is the create POST (the separator's, and since phase 10a-2
        every Common Sense bucket's). A key with no ``title`` is the smart
        filter's replacing PUT, whose only argument is the uri.

        The POSTed ``uri`` is recorded into ``updated_filters`` DELIBERATELY,
        and that is the one amendment to this harness the phase's adjudication
        licensed (`.superpowers/sdd/p10a-facts.md`, Addendum 2 point 1): the
        cell used to hold a plexapi call shape, and now holds the raw-POST
        evidence that replaced it. Same cell, same question ("what filter was
        this collection given"), different grammar.
        """
        args = parse_qs(urlsplit(key).query)
        if "title" not in args:
            return None
        title = args["title"][0]
        collection = FakeCollection(title, rating_key=str(len(self._existing) + 1))
        collection.updated_filters = args.get("uri", [None])[0]
        self._existing[title] = collection
        return None
```

`FakeCollection.updateFilters` STAYS — its job is now to prove it is never
called, and the new assertion from Step 1 (`not isinstance(state["filters"],
dict)`) is what proves it across every scenario.

Then amend the module docstring, which currently says the fixture "has to
reproduce that file byte for byte. Anything else is a behaviour change":

```
The port has to reproduce that file byte for byte. Anything else is a behaviour
change to 305 live collections, whether or not it looks like an improvement.

**One deliberate amendment, phase 10a-2.** The Common Sense family's write path
moved from plexapi's ``createCollection(smart=True, filters=...)`` onto the
raw-POST 9b grammar (roadmap row 185), so the ``filters`` cell -- which recorded
a plexapi CALL SHAPE and not an outcome -- changed for every Common Sense
collection in every scenario, from ``{"contentRating": ["G"]}`` to the POSTed
``uri``. That amendment was adjudicated in advance
(`.superpowers/sdd/p10a-facts.md`, Addendum 2, a user decision), it is the ONLY
cell that moved, it landed as its own reviewed commit, and it is graded by
something this fixture cannot reach: ``tests/test_collection_cs_equivalence.py``
proves the two grammars select the same items. Every other cell -- every action
string, summary, sort, label, member list and poster count -- is the original
capture, unchanged.

Re-capture (only ever on the pre-port commit, or under a written adjudication
like the one above). It writes the fixture and then *fails*, so a capture run
can never be mistaken for a passing gate::
```

Now re-capture, and diff before staging:

```bash
docker compose -p p10a2t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -e AUTOPOSTER_GOLDEN_CAPTURE=1 test \
    sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-t4-capture.log; echo EXIT=$?'
git diff --stat tests/fixtures/collections/golden_port.json
git diff tests/fixtures/collections/golden_port.json > /tmp/golden.diff
grep -c '^[-+]' /tmp/golden.diff
grep '^[-+]' /tmp/golden.diff | grep -v '"filters"' | grep -v '^[-+][-+]'
```

The capture run must FAIL (that is the guard working). The last command must
print **nothing at all**: every changed line is a `"filters"` line. If it prints
anything, **STOP and report** — the diff exceeded what was adjudicated.

Then verify the gate is green again and paste the first three changed cells into
the task report verbatim:

```bash
docker compose -p p10a2t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-t4-golden2.log; echo EXIT=$?'
```

**Scrub check before staging:** the fixture now contains URIs. The fake's
`_uriRoot()` is `server://FAKE-MACHINE-ID/com.plexapp.plugins.library` and its
section key is `42`, so there is nothing real in them — prove it rather than
assume it (global constraint 2):

```bash
grep -niE 'http|token|X-Plex|\.local|[0-9]{1,3}(\.[0-9]{1,3}){3}' \
    tests/fixtures/collections/golden_port.json || echo CLEAN
```

- [ ] **Step 10: Commit the fixture, alone**

```bash
git add tests/test_builder_port_golden.py tests/fixtures/collections/golden_port.json
git commit --no-gpg-sign -m "test(golden): the one adjudicated cell, re-captured deliberately

The golden fixture recorded plexapi's updated_filters -- a CALL SHAPE, not an
outcome -- for every Common Sense collection. The write path moved onto the raw
POST, so that cell now records the POSTed uri instead. This is the one cell the
phase's adjudication licensed to move (.superpowers/sdd/p10a-facts.md Addendum
2, a user decision), it is its own commit rather than a silent re-pin folded
into the port, and the diff is exclusively filters lines -- verified by grepping
the diff for anything else and finding nothing.

The recapture-then-fail guard stays. What the fixture no longer covers is
covered by tests/test_collection_cs_equivalence.py, which proves both grammars
select the same items; a new assertion here pins the negative half, that no
scenario produces a plexapi filters dict at all."
```

- [ ] **Step 11: The mutation proof, and the full suite**

- **Mutation A — the migration.** Revert `definition_hash` to not fold `url`.
  Expected RED:
  `test_the_hash_folds_the_built_uri_so_the_migration_actually_happens`. Note
  in the report that the golden gate stays GREEN under this mutation — that
  asymmetry is precisely why the hash needed its own test.
- **Mutation B — the base.** Change `base="any"` to `base="all"` in
  `reconcile_content_ratings`. Expected RED: the equivalence proof, on every
  addons bucket. This is the membership-changing difference row 185 names,
  caught by the instrument built to catch it.
- **Mutation C — the containment.** Remove the `try/except` around the URL
  build. Expected RED:
  `test_a_bucket_whose_query_cannot_be_built_refuses_only_itself`, with an
  unhandled `TagValueNotFound`.

Backup + cmp for each, real output both halves. Then the whole suite:

```bash
docker compose -p p10a2t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    up -d postgres
docker compose -p p10a2t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10a2t4-suite test \
    sh -c 'timeout -s KILL 1800 pytest -q -n auto 2>&1 | tee /app/.superpowers/run-t4-suite.log; echo EXIT=$? >> /app/.superpowers/run-t4-suite.log'
docker wait p10a2t4-suite
docker rm p10a2t4-suite
docker compose -p p10a2t4 down
```

Read `.superpowers/run-t4-suite.log` from the host. Paste the tail into the
report. Expected: every test passes; the count is 10a-1's 3589 plus this
branch's additions.

- [ ] **Step 12: Prove the second grammar is gone**

```bash
grep -rn "createCollection" src/ | grep -v "create_blank_collection"
grep -rn "updateFilters" src/
grep -rn "smart=True" src/
```

Expected: the first finds only `lists.py`'s list-collection creates (which are
not smart and are not a query grammar); the second and third find **nothing**.
Paste all three into the task report. This is roadmap row 185's own acceptance
criterion, checked against the tree rather than against the intention.

---

## Task 5: The inherited-minor wave

**Files:**
- Modify: `src/autoposter/collections/builders/dynamic.py` (the cap wording, the
  refusal fragment, `_TOKEN`, the `other`-key collision)
- Test: `tests/test_builder_dynamic.py`, `tests/test_collection_dynamic_keys.py`,
  `tests/test_collection_dynamic_types.py`, `tests/test_collection_config.py`

**Interfaces:** none new. Every change is a message, a regex, a refusal or a
test.

**Provenance.** `.superpowers/sdd/branch-review-10a1.md`'s DEFER list, minus the
five items Tasks 1 and 2 already took (T5 M3, N-2, T4's silent None-drop, N-5,
and the sweep-branch rewrite). Each item below names its review id, so a
reviewer can check the list off rather than re-derive it. The three "docs-nits
with no code home" (T1's decade `.regex` citation nuance, T6 M-5's dated-note
cite off-by-one, T6 M-4's untracked report line numbers) go to Task 6's docs
edits; the eight "resolved en route" items and the one "closed by adjudication"
(T5 M1's `<<value>>` list-repr, ruled: stays, upstream parity) need no action
and are recorded as closed in Task 6's evidence sweep.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_malformed_token_is_refused_like_a_wrong_one(session):
    """T5 review, Minor 4. ``_TOKEN`` matches ``<<...>>`` and a half-written
    ``<<key_name>`` matches nothing at all -- so it passed every validator and
    would have been POSTed into a live collection's name exactly as typed,
    which is the one outcome this whole family of refusals exists to prevent.
    """
    with pytest.raises(ValidationError) as caught:
        DynamicParams(type="genre", title_format="Top <<key_name> movies")
    assert "<<" in str(caught.value)
    assert "unbalanced" in str(caught.value).lower()

    # And the well-formed one still passes, so the fix is not "refuse angle
    # brackets".
    assert DynamicParams(type="genre", title_format="Top <<key_name>> movies")


async def test_the_cap_refusal_counts_buckets_and_values_separately(session):
    """T5 review, Minor N-3. The message said "%r enumerates that many values
    there" while counting BUCKETS -- post-merge, post-drop -- so an addons-heavy
    family reported a number the library never said. Both numbers now, because
    the operator's next move (raise the cap, or narrow with include:) depends on
    which one is large."""
    definition = _definition(params={
        "type": "genre", "max_collections": 1,
        "addons": {"Scary": ["Horror", "Drama"]},
    })
    section = FakeSection()
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert len(actions) == 1
    assert "2 value(s)" in actions[0], actions      # Horror, Drama
    assert "collections" in actions[0]
    assert "max_collections" in actions[0]


async def test_a_bucket_refusal_does_not_leak_the_parsers_field_path(session):
    """T5 review, deferred nit. ``parse_filters`` prefixes its refusals with the
    dotted config path it was given, so the operator's sentence read '... and
    params.year: 'Eighties' is not a whole number'. The prefix is this module's
    own argument, not anything the operator wrote."""
    definition = _definition(params={
        "type": "year", "include": ["1990"], "addons": {"Eighties": ["1989"]},
        "other_name": "Everything else",
    })
    section = FakeSection(choices=[
        FakeChoice("1990", "1990"), FakeChoice("1989", "1989"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    refused = [one for one in actions if "refused" in one]
    assert refused, actions
    assert "params.year" not in refused[0], refused[0]
    assert "whole number" in refused[0] or "integer" in refused[0]


async def test_a_real_key_named_other_refuses_the_leftovers_bucket(session):
    """T4 review, deferred minor (upstream-shared). ``other`` is the leftovers
    bucket's literal key (meta.py:1306-1310), so a library that genuinely holds
    a value spelled ``other`` gives one key two meanings -- and the refusal
    hint the emitter prints for the leftovers bucket would be printed for the
    real one. Refused, contained to that bucket, rather than guessed."""
    definition = _definition(params={
        "type": "genre", "include": ["Horror"], "other_name": "Everything else",
    })
    section = FakeSection(choices=[
        FakeChoice("1138", "Horror"), FakeChoice("77", "other"),
    ])
    ctx = _ctx(session, section, definition)

    actions = await DynamicBuilder().apply(ctx)

    assert any("Top Horror movies" in one and "created" in one for one in actions)
    assert any(
        "'other'" in one and "leftovers" in one and "refused" in one
        for one in actions
    ), actions


async def test_a_language_family_expands_a_base_code_at_the_emitter(session):
    """10a-1 review, Minor N-4. The language seam -- a family's values are
    ``choice.key``s, and ``LibraryTagResolver._language_keys`` expands a base
    code to every variant the library holds (plex_search.py:518-538) -- was
    correct by reading and pinned only at the resolver. Pinned here at the
    EMITTER, the way ``decade``'s key/title split already is."""
    definition = _definition(params={
        "type": "audio_language", "include": ["es"],
    })
    section = FakeSection(choices=[
        FakeChoice("es", "Spanish"), FakeChoice("es-419", "Spanish (Latin America)"),
        FakeChoice("en", "English"),
    ])
    ctx = _ctx(session, section, definition)

    await DynamicBuilder().apply(ctx)

    posted = [one for one, _ in section._server.queries]
    assert len(posted) == 1, posted
    assert "audioLanguage=es&or=1&audioLanguage=es-419" in posted[0], posted[0]


@pytest.mark.parametrize("name", ["year", "content_rating", "studio", "network"])
def test_every_type_row_carries_a_sort_and_a_limit_that_exist(name):
    """T2 review, deferred minor: ``limit``/``sort_by`` were pinned only for
    genre and resolution and only transitively. Every row, directly."""
    row = DYNAMIC_TYPES[name]
    assert row.sort_by, name
    for sort in row.sort_by:
        assert sort in KNOWN_SORT_NAMES, (name, sort)
    assert row.limit is None or row.limit >= 1, name


def test_a_bare_scalar_include_is_one_key_not_its_characters():
    """T3 review, deferred minor: ``_strlist``'s non-iterable branch
    (util.py:933) was reachable from a YAML scalar and untested. ``include:
    Horror`` is one key -- not six -- and the params model's ``list[str]``
    coercion is not what makes that true, because ``derive_keys`` is public and
    pure."""
    derived = derive_keys([("Horror", "Horror"), ("Drama", "Drama")],
                          include="Horror")
    assert [one.key for one in derived.keys] == ["Horror"]


def test_the_registry_names_every_builder_when_one_is_misspelled():
    """T5 review, deferred minor: the "known builders" message was unpinned, so
    a builder added to the registry without a catalog row would have quietly
    stopped appearing in the one message an operator sees when they typo."""
    with pytest.raises(ValidationError) as caught:
        CollectionDefinition(title="X", builder="dynamik", params={})
    message = str(caught.value)
    for name in REGISTRY:
        assert name in message, name
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p p10a2t5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_dynamic.py tests/test_collection_dynamic_keys.py tests/test_collection_dynamic_types.py tests/test_collection_config.py 2>&1 | tee /app/.superpowers/run-t5-red1.log; echo EXIT=$?'
```

- [ ] **Step 3: Harden `_TOKEN`**

`dynamic.py:163-165` gains a companion, and the validator gains one check:

```python
# Anything written as ``<<...>>``. Non-greedy on the inside so two tokens on one
# line are two matches rather than one span from the first to the last.
_TOKEN = re.compile(r"<<[^<>]*>>")

# What is left after every well-formed token is removed. A half-written
# ``<<key_name>`` matches ``_TOKEN`` not at all, so without this it passed every
# validator and shipped literally into a live collection's name -- the exact
# outcome the token check exists to prevent, reached by typing one bracket
# fewer.
_UNBALANCED = re.compile(r"<<|>>")
```

In `_every_token_must_be_one_something_resolves`, after the per-token loop, per
`(where, text, allowed)`:

```python
            if _UNBALANCED.search(_TOKEN.sub("", text)):
                raise ValueError(
                    "`%s` has unbalanced token delimiters: %r. A token is "
                    "written `<<name>>` with two brackets on each side, and a "
                    "half-written one matches nothing here -- it would be "
                    "written into the collection's name exactly as it stands, "
                    "which is what this check exists to prevent" % (where, text)
                )
```

- [ ] **Step 4: Fix the cap wording, the refusal fragment and the `other` key**

The cap message (`dynamic.py:524-533`) — count both:

```python
        if len(titled) > params.max_collections:
            return self._refused(ctx, definition.title, (
                "this would create %d collections in %r -- %r reports %d "
                "value(s) there, which `include:`, `exclude:` and `addons:` "
                "narrow to that many buckets -- and `max_collections` is %d, so "
                "nothing was created. Narrow the family with `include:` or "
                "`exclude:`, or raise `max_collections` past %d if that is "
                "really what you want"
                % (len(titled), ctx.library, params.type, len(enumerated),
                   params.max_collections, len(titled))
            ))
```

The refusal fragment (`dynamic.py:590-604`) — strip the parser's own prefix:

```python
                # ``parse_filters`` prefixes every refusal with the dotted
                # config path it was handed, which is this module's argument
                # (``field="params"``) and not anything the operator wrote. The
                # prefix is deterministic, so removing exactly it is safe and
                # falling back to the whole message is honest.
                reason = str(refusal).removeprefix(
                    "params.%s: " % row.search_key
                )
                actions.append(
                    "refused %r in the %r family: it asks this library for %s "
                    "as %r values, and %s.%s"
                    % (
                        unit.title, definition.title,
                        ", ".join(repr(one) for one in values), params.type,
                        reason,
                        ...
                    )
                )
```

The `other`-key collision — after `titled` is computed and before the loop:

```python
        # ``OTHER_KEY`` is the leftovers bucket's literal key (meta.py:
        # 1306-1310), so a library that genuinely holds a value spelled "other"
        # gives one key two meanings: the emitter's leftovers hint would be
        # printed for the real value's bucket, and an operator's `exclude:
        # [other]` would be ambiguous. Refused for that one bucket rather than
        # guessed. Upstream shares the collision and does not notice it.
        ambiguous = params.other_name is not None and params.include and any(
            unit.key == OTHER_KEY and unit.title != _other_title_of(titled)
            for unit in titled
        )
```

That formulation needs a helper nobody wants. Write it as the simpler, exact
check instead — the leftovers bucket is the LAST entry `family_titles` claims
(`dynamic_titles.py:331-335`), so a second `OTHER_KEY` entry can only be a real
value:

```python
        other_keys = [
            index for index, unit in enumerate(titled) if unit.key == OTHER_KEY
        ]
        if len(other_keys) > 1:
            # ``family_titles`` claims the leftovers bucket LAST
            # (dynamic_titles.py:331-335), so any earlier OTHER_KEY entry is a
            # value the library actually holds.
            real = titled[other_keys[0]]
            titled = tuple(
                unit for index, unit in enumerate(titled)
                if index != other_keys[0]
            )
            actions.append(
                "refused %r: %r holds a %r value spelled %r, which is also the "
                "leftovers bucket's own key, so one key would mean two things "
                "here -- `exclude:` or `key_name_override:` could not tell them "
                "apart either. Drop `other_name:` to build the real value's "
                "collection, or `exclude: [%s]` to build only the leftovers"
                % (real.title, ctx.library, params.type, OTHER_KEY, OTHER_KEY)
            )
```

(`actions` was already moved above this point by Task 2 Step 6.)

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker compose -p p10a2t5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_dynamic.py tests/test_collection_dynamic_keys.py tests/test_collection_dynamic_types.py tests/test_collection_config.py tests/test_collection_dynamic_oracle.py 2>&1 | tee /app/.superpowers/run-t5-green1.log; echo EXIT=$?'
```

The oracle file is in the run because `derive_keys` and `family_titles` are
pinned there and this task must not move a pinned answer.

- [ ] **Step 6: Mutation proof and commit**

- **Mutation A.** Revert `_UNBALANCED` to `re.compile(r"XXXX")`. Expected RED:
  `test_a_malformed_token_is_refused_like_a_wrong_one`.
- **Mutation B.** Change the `other_keys` slice from `> 1` to `> 2`. Expected
  RED: `test_a_real_key_named_other_refuses_the_leftovers_bucket`.

```bash
docker compose -p p10a2t5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10a2t5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py tests/test_collection_cs_equivalence.py 2>&1 | tee /app/.superpowers/run-t5-gates.log; echo EXIT=$?'
git diff --stat HEAD~1 -- tests/fixtures/collections/golden_port.json
docker compose -p p10a2t5 down
```

```bash
git add src/autoposter/collections/builders/dynamic.py \
        tests/test_builder_dynamic.py tests/test_collection_dynamic_keys.py \
        tests/test_collection_dynamic_types.py tests/test_collection_config.py
git commit --no-gpg-sign -m "fix(dynamic): the 10a-1 review's deferred minor wave

An unbalanced token delimiter now refuses instead of shipping into a live
collection's name; the fan-out cap reports the enumeration count and the bucket
count as the different numbers they are; a bucket refusal no longer carries the
parser's own field path; and a library value literally spelled 'other' refuses
that one bucket instead of quietly meaning two things.

Plus five coverage gaps the review named: a language family's base-code
expansion pinned at the emitter rather than only at the resolver, sort and limit
asserted on every type row instead of two, _strlist's bare-scalar branch, and
the registry's known-builders message."
```

---

## Task 6: The wrap

**Files:**
- Modify: `src/autoposter/collections/catalog.py` (eleven descriptions)
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (rows 102,
  185, 135)
- Modify: `docs/research/kometa-collections.md` (the row-185 note, if it carries
  one)
- Create (gitignored, pasted into the PR): `.superpowers/sdd/p10a-2-pr-body.md`

- [ ] **Step 1: Re-point the eleven presets, without flipping one**

The eleven are **nine** written-out `Preset(...)` entries plus the **three**
generated by the `LOCATION_PRESETS` comprehension — no: eight written-out plus
three generated. Verify the count before editing and record it:

```bash
grep -n "gated_row=DYNAMIC_ENGINE_ROW" src/autoposter/collections/catalog.py
```

Expected: **nine** lines — `content_genres` (:796), `content_franchises` (:814),
the `LOCATION_PRESETS` comprehension (:1280, which generates
`location_country` / `location_region` / `location_continent`),
`media_audio_language` (:1382), `media_subtitle_language` (:1402),
`production_studio` (:1576), `production_network` (:1604), `time_year` (:1626),
`time_decade` (:1641) — for **eleven** presets. If the count differs, record
what you found and re-point what is there; the number is a fact about the file,
not a target.

Each description's blocker clause moves from "the engine is missing" to the
preset-expansion story. The engine is not missing any more — it shipped in 10a-1
and got its lifecycle here — so leaving the old text would be a false claim
about this service. Example, `content_genres`:

```python
        description=(
            "One collection per genre the library actually holds -- Kometa's "
            "largest pack. The per-value engine it needs SHIPPED in phase 10a "
            "(`builder: dynamic`, `type: genre`), so an operator can build this "
            "family today by writing a definition. What is still missing is the "
            "PRESET: turning one catalog row into a family of collections is the "
            "preset-expansion story, phase 10b, and it is what row %d tracks. "
            "The genre attribute's own caveat stands and is why the family is "
            "built by SEARCH rather than from the section listing, which Phase "
            "9a's probe found truncates to two tags per item (row %d)."
            % (DYNAMIC_ENGINE_ROW, STRANDED_FILTER_ROW)
        ),
```

Apply the same move to the other ten, keeping each one's own subject matter.
**Three constraints on the rewrite, each with the test that enforces it:**

1. `readiness` stays `GATED` and `gated_row` stays `DYNAMIC_ENGINE_ROW` on all
   eleven — `test_the_catalog_table_checksum`
   (`tests/test_collection_catalog.py:201-225`) counts readiness per category
   and must not move.
2. `media_audio_language`, `media_subtitle_language` and `production_network`
   must keep the substrings `"live probe"` and `"enumerat"` in their
   descriptions, and `media_audio_language` must keep `"row %d" %
   STRANDED_FILTER_ROW` — `test_the_three_presets_9b_readjudicated_stay_gated_on_the_engine_row`
   (`:1389-1416`) asserts all four.
3. Every roadmap row a description cites must exist — the catalog's own gating
   tests read the roadmap `.md` and check that (`:467`).

- [ ] **Step 2: Close roadmap rows 102 and 185**

Row 185 (`:281`) — replace the row's status marker and append:

> **CLOSED by phase 10a-2.** The Common Sense family writes
> `smart.create_smart_collection` / `smart.update_smart_collection` over a
> `build_search_url` query on an `any:` base; `section.createCollection(smart=
> True, filters=…)` and `Collection.updateFilters(...)` appear nowhere in
> `src/` any more, verified by grep against the shipped tree. Addendum 2's four
> bindings all discharged: the golden fixture's one `filters` cell per Common
> Sense collection was amended deliberately as its own reviewed commit with the
> recapture-then-fail guard intact and the diff proven to contain nothing else;
> the mandatory equivalence proof is `tests/test_collection_cs_equivalence.py`,
> which runs plexapi's own `_buildSearchKey` and this engine's
> `build_search_url` for every bucket shape in the shipped table on both library
> types and asserts identical member sets under four cited premises; the
> one-time migration (one update PUT per Common Sense collection, membership
> unchanged) is stated in the PR body with the operator acceptance procedure;
> and this row closes here.

Row 102 (`:204`) — append:

> **CLOSED by phase 10a-2.** The lifecycle half shipped: a collection carrying
> a dynamic family's label that the family did not rebuild this pass is a sweep
> candidate like any other and goes through every guard already there — the
> ownership label, a `managed_collections` row, `protect_labels`,
> `delete_unconfigured`, `max_deletes`, `dry_run` and the `EventLog` audit —
> and a family that did not run (outside its schedule, or refused) protects all
> of its collections and says so once. Delete-below-minimum rides the same
> mechanism: `params.minimum_items` (default `None`, because upstream has no
> per-key minimum for any library dynamic type) takes a below-threshold key out
> of the pass's record and the sweep does the rest. The Common Sense port
> (decomposition step 5 / row 185) closed with it. **Still gated, and 10b's:**
> the eleven `DYNAMIC_ENGINE_ROW` presets. Their blocker text now says so — the
> engine is here, the preset EXPANSION is 10b's preset-expansion story — and
> not one preset's readiness moved in this phase (adjudication C7), verified
> against the diff.

- [ ] **Step 3: Row 135's clause**

The 10a-1 fix wave (`e96c552`) already added the honest backstop clause. Add
only what this phase changed:

> **Phase 10a-2:** the gap is unchanged — a dynamic family's titles are still
> invisible to `_titles_must_not_collide` — but the run-time consequence is no
> longer only a filter fight. A family's own delete sweep now enumerates by
> label, so two definitions whose generated titles collide can also take turns
> putting each other's collection into the other's non-regenerated set. The
> guards hold (both would need the ownership label, a row, and
> `delete_unconfigured`), and the fix is the same one the row's first sentence
> names.

- [ ] **Step 4: The operator acceptance procedure**

Written into the PR body, in generic wording, **with no hostname, no library
name, no token and no URL**. It is a post-deploy procedure because the migration
is a deploy-time event and cannot be rehearsed against the fixture.

```markdown
## Accepting this after it deploys

The Common Sense collections' stored filters are rewritten once, on the first
pass after this deploys. Membership does not change; the query grammar does.
Three checks, in order, none of which needs a shell on the server:

1. **On the first pass after the deploy**, the run report should show one
   `updated '<bucket title>'` line per Common Sense collection in each library,
   and nothing else about that family — no `created`, no `refused`, no
   `conflict`, no poster line that was not already there every pass.
2. **On the pass after that**, the same family should produce **no lines at
   all**. The migration is one pass. A second pass that still reports updates
   means the stored hash is not being written, which is a bug and not a slow
   rollout — capture that report and stop.
3. **In Plex**, open one age-bucket collection in each library and compare its
   item count with what it held before the deploy. They must match. If a
   collection is empty or has obviously changed size, the filter it was given
   is wrong: the collection can be restored by deleting its
   `managed_collections` row and letting the next pass rebuild it, and the
   report from step 1 is what to attach to the bug.

The delete sweep is unchanged in posture by this release: it is still off unless
`collections.delete_unconfigured` is set, and it still refuses entirely past
`collections.max_deletes`. If you have `builder: dynamic` families and you are
turning the sweep on for the first time, do it with
`collections.apply_to_plex` off first and read the `would delete` lines — a
family that narrowed will list every collection it no longer builds.
```

- [ ] **Step 5: Write the PR body**

`.superpowers/sdd/p10a-2-pr-body.md` — gitignored, pasted into the PR rather
than committed. It must state, in this order:

- what shipped (the five task deliverables, one line each);
- **the production migration**, in Addendum 2 point 3's own terms: on first
  deploy every Common Sense collection's stored server-side filter string
  changes, so its `definition_hash` no longer matches and the pass issues one
  update PUT per collection; **membership is unchanged**, and the proof of that
  is `tests/test_collection_cs_equivalence.py` rather than the golden gate;
- **the deliberate fixture amendment**: what cell, why the golden gate could not
  cover it, that it landed as its own reviewed commit, that the diff was proven
  to contain nothing but `filters` lines, and that the recapture-then-fail guard
  stayed;
- **what now deletes**, and what still guards it: the family sweep's five
  guards, the fail-closed rule for a family that did not run, and
  `minimum_items`' opt-in default;
- the operator acceptance procedure from Step 4, verbatim;
- what this does NOT do: no preset flips, no re-derivation of the Common Sense
  family onto `derive_keys` (Addendum 3), no enumeration-time minimum, no
  `sync:` key;
- rows 102 and 185 closed, row 135's clause;
- **no AI attribution, no mention of how the work was produced, no hostname, no
  token, no URL.**

- [ ] **Step 6: Verify C7 against the diff, not the intention**

```bash
git diff --stat origin/main -- src/autoposter/collections/sources.py
git diff origin/main -- src/autoposter/collections/catalog.py | grep '^[-+]' | grep -E 'readiness|gated_row|Preset\(|key='
grep -n "builder=\"dynamic\"\|builder='dynamic'" src/autoposter/collections/catalog.py src/autoposter/collections/sources.py
```

Expected: the first produces **no output**; the second produces **no output**
(only description lines changed); the third finds nothing. Paste all three into
the task report. If the second is non-empty, a preset flipped and C7 is
violated: **STOP and report** rather than adjusting the expectation.

- [ ] **Step 7: The evidence sweep**

Assemble in the task report, each with the run it came from:

1. Every task's mutation proof, RED and GREEN, real output only, with the
   `cmp … && echo RESTORED-EXACT` line for each.
2. The golden diff: `git diff origin/main -- tests/fixtures/collections/golden_port.json`
   piped through the "anything but a filters line" grep, showing nothing.
3. Task 4 Step 12's three greps proving the second grammar is gone.
4. The equivalence proof's side-by-side output (Task 3 Step 5), and the scrub
   grep over it and over the amended fixture.
5. The DEFER-list checklist: every item from
   `.superpowers/sdd/branch-review-10a1.md`'s "DEFER to 10a-2" block, with the
   task that took it — including the eight "resolved en route" and the one
   "closed by adjudication" recorded as needing no action, and the three
   docs-nits with the edit that carried them.
6. The final full-suite run, `ruff check src tests`, and the golden gate.
7. The three C7 checks from Step 6.

- [ ] **Step 8: Gate and commit**

```bash
docker compose -p p10a2t6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p10a2t6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py tests/test_builder_port_golden.py tests/test_collection_cs_equivalence.py 2>&1 | tee /app/.superpowers/run-t6-gate.log; echo EXIT=$?'
docker compose -p p10a2t6 down
```

`tests/test_collection_catalog.py` is in that run for a specific reason: its
gating tests read the roadmap `.md` and assert that every gated row cites a row
that exists, so a roadmap edit is the one docs change that can redden the suite.

Then the whole suite, detached, no `--rm`, foreground wait, teed inside the
container:

```bash
docker compose -p p10a2t6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    up -d postgres
docker compose -p p10a2t6 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10a2t6-suite test \
    sh -c 'timeout -s KILL 1800 pytest -q -n auto 2>&1 | tee /app/.superpowers/run-t6-suite.log; echo EXIT=$? >> /app/.superpowers/run-t6-suite.log'
docker wait p10a2t6-suite
docker rm p10a2t6-suite
docker compose -p p10a2t6 down
```

```bash
git add src/autoposter/collections/catalog.py \
        docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs: close rows 102 and 185, re-point the eleven gated presets

The dynamic engine is no longer missing, so eleven preset descriptions stop
saying it is: each now says the engine shipped, that a definition can build the
family today, and that what is still gated is the preset EXPANSION -- phase 10b.
Not one readiness or gated_row moved (adjudication C7), verified against the
diff rather than against the intention.

Row 102 closes on the lifecycle half and delete-below-minimum; row 185 closes on
the grammar unification, with all four of Addendum 2's bindings discharged. Row
135's clause records that the invisible-titles gap now has a sweep consequence
as well as a filter-fight one."
```

---

## Self-Review

**1. Spec coverage.**

| Requirement | Where |
| --- | --- |
| C4 — lifecycle by label, through OUR guards, never a bare `library.delete` | T1 (the sweep folds the family record into `managed`; every guard is the existing one) |
| C4 — delete-below-minimum rides the same labeled sweep | T2 (a below-minimum key leaves the record; the sweep does the deleting) |
| C8 — the `sync:` key is not a per-definition delete switch | T1 Step 7 (the refusal's reason is rewritten to the shipped truth) |
| Addendum 2 (a) — the fixture's one cell, its own reviewed commit, guard intact | T4 Steps 9–10 (two commits; the "anything but filters" grep; the recapture-then-fail guard and its docstring amendment) |
| Addendum 2 (b) — MANDATORY oracle-grade equivalence proof, every bucket shape incl. addons and the other-complement | T3 (both drivers run the real code; `covered` asserts each shape was exercised, not merely available; both library types and both show field spellings) |
| Addendum 2 (c) — the migration stated in the PR body | T6 Steps 4–5 |
| Addendum 2 (d) — row 185 closes | T6 Step 2, with T4 Step 12's grep as its evidence |
| Addendum 3 — our empty-bucket semantics preserved, Kometa's drop NOT ported | T4 Step 5 (`if not bucket.values: continue` kept, with the comment naming the sweep consequence) and `test_an_empty_bucket_is_still_never_created_and_never_deleted`, which also asserts `cs_bucket.titles()` still names it |
| C7 — zero preset flips | T6 Steps 1 and 6, checked against the diff |
| Every DEFER-list item | T1 (T5 M3, N-2), T2 (T4 None-drop, N-5), T5 (N-3, N-4, `_TOKEN`, `params.year`, `other` key, `_strlist`, sort/limit, registry message), T6 Step 7 item 5 (the three docs-nits and the nine no-action items, recorded) |
| The operator acceptance procedure, generic, no hostnames | T6 Step 4 |

**Two DEFER items deliberately closed as no-action**, recorded rather than
implemented: T5 M1's `<<value>>` list-repr (already ruled by the branch review —
stays, upstream parity, and `dynamic.py:174-179` explains it) and T3's "dead
or-disjunct in its test" (a test-internal redundancy the review itself rated a
nit; removing it would change a pinned oracle case's shape for no gain — say so
in the sweep rather than touching an oracle).

**2. Placeholder scan.** One place names a decision the plan cannot make for the
implementer, and it carries the exact command that decides it rather than
leaving a choice: T4 Step 5's import-cycle check
(`python -c "import autoposter.collections.reconcile"` decides whether
`LibraryTagResolver` goes at module level or inside the function). T3's driver
loading is settled against `tests/test_collection_search_oracle.py:104,276` and
the absence of any `__init__.py` under `tests/oracle/`, with the loader written
out. T6 Step 1's preset count is stated as a fact to verify with a named
command, with the rule for what to do if it differs. Nothing else is deferred.

**3. Type consistency.** `generated_titles(run_cache, definition) -> set[str] |
None` is produced in T1 as both a module function and a `DynamicBuilder`
method, read in T1's `engine._family_state` off the REGISTRY entry, and read by
name in T1's and T2's tests. `_generated_key(label) -> str` is produced in T1
and imported by T1's and T2's tests. The `generated` set inside `apply` is the
same object the run cache holds, which is what makes T2's `discard` reach the
sweep — pinned by T2's Mutation B. `_family_state -> (dict[str,str],
dict[str,set[str]])` and `_why(str | None) -> str` are produced and consumed
only inside `engine._sweep`. `count_matches(section, url) -> int` is produced in
T2's `smart.py` and consumed in T2's `dynamic.py` and by `require_matches`.
`definition_hash(bucket, settings=None, url="")` gains a third parameter in T4
and every caller in `reconcile.py` passes it positionally as the third argument;
the separator's `SEPARATOR_HASH` is a module constant computed elsewhere and is
unaffected. `reconcile_content_ratings(..., resolver=None)` is produced in T4
and passed by `cs_bucket.apply`; every other caller is a test and takes the
fallback. `member_sets.members(query, library, libtype) -> set[str]`,
`plexapi_side.old_query(libtype, values, ratings, field_key) -> str` and
`ours_side.new_query(libtype, values, present) -> str` are produced in T3 and
consumed only by T3's gate.

---

## CONTRADICTION FLAG

Three places the evidence disagrees with itself. **All three are planned the
adjudicated way regardless**, per the brief.

### FLAG 1 — Addendum 2 point 1 ("one cell") versus point 3 ("a burst of 'updated the smart filter'")

**The fact.** Point 3 describes what the operator sees as "a one-pass burst of
*'updated the smart filter'* actions". That is the exact wording of
`smart.reconcile_smart_collection`'s action string
(`smart.py:317-320`: `"updated the smart filter of %r (%d item(s) match now)"`).
The Common Sense reconciler's is `"updated %r"` (`reconcile.py:659`). So point 3
reads as though the family is to be routed through `reconcile_smart_collection`.

**The contradiction.** Point 1 scopes the fixture amendment to "the ONE
documented cell change (`updated_filters` → raw-POST evidence)". Routing the
family through `reconcile_smart_collection` changes far more than one cell: every
`created`/`updated` action string in every scenario, the per-bucket poster kinds
(`content_rating` / `content_rating_other` become `None`/`None`), the derived
per-bucket summaries (that reconciler passes the definition's summary, and this
family has none), and it adds a `require_matches` read per bucket. That is a
behaviour change to 305 live collections, which is the thing the golden gate
exists to prevent — and point 1 licensed one cell.

**What the plan does about it.** It reads point 1 as the OPERATIVE constraint
and point 3 as prose describing the migration's *shape* (one update PUT per
collection, nothing else), not its *string*. So `reconcile_content_ratings`
keeps its reconciler and every one of its action strings, and only its two write
calls and its hash move. The observable migration is exactly what point 3
promises — one update per Common Sense collection, membership unchanged, nothing
else in the report — under the wording `"updated 'Age 1+ Movies'"`. If the
controller intended the literal string, that is T4 re-planned as a much larger
port with a much larger fixture amendment, and the operator acceptance procedure
in T6 Step 4 changes its step 1 accordingly.

### FLAG 2 — the 10a-2 outline's T3 versus Addendum 3

**The fact.** The 10a-1 plan's outline says T3 turns `cs_bucket` into "a
`dynamic` definition (`type: content_rating`, `include` + `addons` from the
shipped table, `title_format: Age <<key_name>>+ <<library_typeU>>s`, the `other`
bucket)" **or** keeps its builder and switches its write path, with the ruling
deciding which.

**The contradiction.** Addendum 3 rules that the port must PRESERVE our
empty-bucket semantics, and `derive_keys` cannot: it never yields a key the
library does not hold and whose addons it does not hold either
(`dynamic_keys.py:141-150`), which is Kometa's derive-time drop
(`meta.py:1224-1225`) and which 10a-1's own test asserts as the deliberate
difference (`tests/test_collection_dynamic_keys.py:33`, `dropped == 2`). A CS
family built on `derive_keys` would lose those titles from `cs_bucket.titles()`,
and an existing-but-now-empty Common Sense collection would become an ordinary
sweep orphan — which is exactly the outcome Addendum 3 names and forbids.

**What the plan does about it.** It takes the second branch, which Addendum 3
requires: `buckets.derive_buckets` stays, `cs_bucket.titles()` stays, the
empty-bucket skip stays, and only the write path moves. T4's
`test_an_empty_bucket_is_still_never_created_and_never_deleted` pins both halves
— nothing is created for it, and its title is still in `titles()`, which is what
keeps it out of every sweep's candidate set. The outline's first branch is not
available under Addendum 3, and this plan says so rather than choosing between
them.

### FLAG 3 — "the eleven 102-gated presets"

**The fact.** The brief, the 10a-1 plan's T5 outline and roadmap row 102 all say
"the eleven `DYNAMIC_ENGINE_ROW` presets". `grep -n
"gated_row=DYNAMIC_ENGINE_ROW" src/autoposter/collections/catalog.py` finds
**nine** lines, because one of them is inside the `LOCATION_PRESETS`
comprehension (`catalog.py:1267-1283`) which generates three presets from
`_LOCATION_PACKS` (`:1255-1265`). Eight written-out entries plus three generated
is eleven presets from nine lines. Two further presets mention
`DYNAMIC_ENGINE_ROW` in their descriptions while being gated elsewhere —
`content_based_on` (gated on `KEYWORD_RESOLUTION_ROW`, `:836`) and
`media_aspect` (gated on `FILTER_TIER_TWO_ROW`, `:1351`) — and a naive
grep count of the *symbol* returns fourteen.

**The contradiction.** None, once resolved — but "eleven" and a grep that
returns nine or fourteen is exactly the kind of mismatch that produces either a
missed preset or an accidental flip.

**What the plan does about it.** T6 Step 1 states the resolution, gives the
command, names all eleven presets and the two look-alikes, and instructs the
implementer to record what the grep actually found rather than to make it agree
with the number. The two look-alikes' descriptions each contain a sentence
saying row 102 landing would NOT close them (`content_based_on`: "Not the
per-value engine: based.yml is a FIXED four-collection pack"), so both need a
one-clause update to stay true now that row 102 HAS landed — folded into T6
Step 1 as a twelfth and thirteenth edit that changes no gating.
