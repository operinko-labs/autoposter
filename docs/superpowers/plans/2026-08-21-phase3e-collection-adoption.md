# Phase 3e: Taking Over the Existing Collections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let this service take ownership of the collections the previous tool created, instead of reporting every one of them as a conflict forever.

**Architecture:** Both reconcilers already refuse to touch a collection that lacks our ownership label. This adds one explicit, opt-in step at exactly that point: if the colliding collection carries a label belonging to a tool we are replacing, claim it by adding our own label, then reconcile it normally. Nothing else about the ownership boundary changes.

**Tech Stack:** Python 3.13, plexapi 4.18.2, async SQLAlchemy 2.0 + asyncpg, PostgreSQL 18.

## Why this is needed

The library holds 49 collections labelled `Kometa`. Of those, **47 have titles this service would create itself** — 37 Common Sense age buckets, the IMDb chart collections, and the seven Oscars collections. The other two are both `Ratings Collections`, a blank separator this service does not implement.

Today the first reconcile pass finds `Age 17+ Movies` already present carrying `Kometa` rather than `autoposter`, reports a conflict, and leaves it alone. That is the correct default — but it means the migration never completes: the old collections stay, ours are never created, and every pass reports 47 conflicts indefinitely.

## Global Constraints

- **An unlabelled collection is never adopted.** Only a collection carrying a label explicitly listed as a prior tool's is eligible. The Movies library holds five collections the operator made by hand and 269 Plex franchise collections, none of which carry any label — those must remain untouchable, and a title collision with one must still be reported as a conflict.
- **Only titles this service would create itself are ever considered.** Adoption happens at the collision point inside the reconcile loop, so a `Kometa` collection whose title we do not manage is never touched.
- **Adoption is opt-in and off by default**, and it is a Plex write, so it is additionally gated by the existing `apply_to_plex`.
- **The prior tool's label is kept by default.** Removing it is a separate opt-in. Keeping both is reversible; a run that strips labels is not.
- **Nothing is ever deleted** — no deletion path exists in the collections package and none is added here.
- **Never call `.refresh()` on a Plex object.** A guard test forbids it; `.reload()` is fine.
- **All database timestamps come from `func.now()`**, never `datetime.now()`.
- **Run tests with `rtk proxy python -m pytest ... -v`** — a bare `python -m pytest` is mangled by a shell hook.
- **Commit with `git commit --no-gpg-sign`** — GPG signing times out here.
- **Never `docker compose down -v`.** PostgreSQL 18 runs on `localhost:5433`.
- **No test may make a real outbound request** — an autouse fixture in `tests/conftest.py` enforces this.
- Baseline on branch start: 826 passed, 5 skipped, `ruff check src tests` clean. Both must stay green.
- The Postgres container clock steps backwards by up to 10 seconds between transactions, so time-sensitive tests flake at roughly 5%. Re-run before concluding a failure is real.

---

## Task 1: Claiming ownership

**Files:**
- Modify: `src/autoposter/collections/reconcile.py`
- Modify: `src/autoposter/collections/lists.py`
- Modify: `src/autoposter/config/schema.py`
- Modify: `config/autoposter.example.yaml`
- Test: `tests/test_collection_adoption.py`

**Interfaces:**
- Produces: `prior_tool_label(collection, adopt_from: list[str]) -> str | None` in `reconcile.py`, returning the first prior-tool label the collection carries; and `claim_ownership(collection, label: str, prior: str, remove_prior: bool) -> None`.
- Config: `collections.adopt: bool = False`, `collections.adopt_from: list[str] = ["Kometa"]`, `collections.adopt_removes_prior_label: bool = False`.

**Notes for the implementer:**

- `has_label` already forces an explicit `reload()` before reading `labels`, because plexapi never populates that from `section.collections()` results. `prior_tool_label` must not re-read stale state — reuse the reloaded object rather than reloading again per candidate label.
- Both reconcilers have the same collision branch (`reconcile.py` around line 84, `lists.py` around line 91). Put the decision in one shared helper and call it from both; two copies of an ownership rule is exactly the divergence this project has been careful to avoid.
- The decision at a collision is: carries our label → proceed as today. Carries a prior tool's label **and** `adopt` is on → claim it, record the action, and proceed to reconcile normally. Anything else → conflict, untouched, as today.
- Adding a label is a Plex write, so under `dry_run` report what would be claimed and claim nothing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_adoption.py`:

```python
"""Taking over collections created by the tool being replaced.

The dangerous direction here is adopting something that is not ours to adopt:
the Movies library holds 269 Plex franchise collections and five the operator
made by hand, none of which carry a label. Those must stay untouchable.
"""
import pytest

from autoposter.collections.reconcile import claim_ownership, prior_tool_label

LABEL = "autoposter"


class FakeCollection:
    def __init__(self, title, labels=()):
        self.title = title
        self._labels = [type("L", (), {"tag": t})() for t in labels]
        self.added = []
        self.removed = []

    @property
    def labels(self):
        return self._labels

    def reload(self):
        pass

    def addLabel(self, labels, locked=True):
        self.added.append(labels)
        self._labels.append(type("L", (), {"tag": labels})())

    def removeLabel(self, labels, locked=True):
        self.removed.append(labels)
        self._labels = [x for x in self._labels if x.tag != labels]


def test_a_prior_tool_label_is_recognised():
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    assert prior_tool_label(collection, ["Kometa"]) == "Kometa"


def test_an_unlabelled_collection_is_never_eligible():
    """The operator's hand-made collections carry no label at all."""
    assert prior_tool_label(FakeCollection("The Ninja Trilogy"), ["Kometa"]) is None


def test_another_tools_label_is_not_eligible_unless_configured():
    """Maintainerr's 'Deleted Soon' carries its own label and is not ours."""
    collection = FakeCollection("Deleted Soon", labels=["Collection managed by Maintainerr"])
    assert prior_tool_label(collection, ["Kometa"]) is None


def test_an_empty_adopt_list_disables_recognition_entirely():
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    assert prior_tool_label(collection, []) is None


def test_claiming_adds_our_label():
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    claim_ownership(collection, LABEL, "Kometa", remove_prior=False)
    assert collection.added == [LABEL]


def test_the_prior_label_is_kept_by_default():
    """Keeping both is reversible; stripping labels is not."""
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    claim_ownership(collection, LABEL, "Kometa", remove_prior=False)
    assert collection.removed == []
    assert {label.tag for label in collection.labels} == {"Kometa", LABEL}


def test_the_prior_label_can_be_removed_on_request():
    collection = FakeCollection("Age 17+ Movies", labels=["Kometa"])
    claim_ownership(collection, LABEL, "Kometa", remove_prior=True)
    assert collection.removed == ["Kometa"]
    assert {label.tag for label in collection.labels} == {LABEL}


def test_extra_labels_are_left_alone():
    """The Oscars year collections also carry 'Oscars Winners Awards'."""
    collection = FakeCollection("Oscars Winners 2026", labels=["Oscars Winners Awards", "Kometa"])
    claim_ownership(collection, LABEL, "Kometa", remove_prior=True)
    assert "Oscars Winners Awards" in {label.tag for label in collection.labels}
```

Then add, in the same file, tests driving the real reconcilers end to end — reusing the fakes already in `tests/test_collection_reconcile.py` and `tests/test_collection_lists.py` rather than inventing new ones:

- with `adopt` off, a `Kometa`-labelled collision is still reported as a conflict and not modified;
- with `adopt` on, it is claimed and then reconciled, and a `ManagedCollection` row is recorded;
- with `adopt` on, an **unlabelled** collision is still a conflict and is not claimed;
- under `dry_run`, an eligible collection is reported as "would adopt" and no label is added;
- the same holds for both the smart-collection reconciler and the list reconciler.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_adoption.py -v`
Expected: FAIL with `ImportError: cannot import name 'prior_tool_label'`

- [ ] **Step 3: Add the config**

In `src/autoposter/config/schema.py`, on `CollectionsConfig`:

```python
    # Take over collections created by a tool this service replaces. Off by
    # default: it is a Plex write against collections we did not create, and
    # it should happen once, deliberately, as part of cutover.
    adopt: bool = False
    # Labels belonging to tools being replaced. A collection carrying one of
    # these, whose title this service manages, is eligible to be claimed. A
    # collection with no label is never eligible -- those are the operator's.
    adopt_from: list[str] = Field(default_factory=lambda: ["Kometa"])
    # Strip the prior tool's label once claimed. Keeping it is reversible;
    # removing it is not, so it is opt-in.
    adopt_removes_prior_label: bool = False
```

Mirror all three into `config/autoposter.example.yaml` under `collections:`, with those comments condensed. `tests/test_example_config_matches_schema.py` asserts every key in the example exists in the schema.

- [ ] **Step 4: Implement and wire both reconcilers**

Add `prior_tool_label` and `claim_ownership` to `reconcile.py`, and call them from the collision branch in both `reconcile.py` and `lists.py`. `reconcile_content_ratings` and `reconcile_list_collection` each need the adoption settings; pass them explicitly rather than threading the whole `Config` into modules that currently take none.

- [ ] **Step 5: Run the suite, ruff and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter config/autoposter.example.yaml tests/test_collection_adoption.py
git commit --no-gpg-sign -m "Claim collections created by the tool being replaced"
```

---

## Task 2: Reporting what is left behind

After adoption there will still be collections carrying a prior tool's label that this service does not manage — in the current library, two `Ratings Collections` separators. The operator should be told, once, rather than discovering them later.

**Files:**
- Modify: `src/autoposter/collections/service.py`
- Test: `tests/test_collection_leftovers.py`

**Interfaces:**
- Produces: `unmanaged_prior_collections(collections, managed_titles: set[str], adopt_from: list[str]) -> list[str]`.

**Notes for the implementer:**

- It reports titles only. It must not modify, claim or delete anything — there is no argument for a service deciding what to do with a collection it does not manage.
- Call it from `reconcile_libraries` after the per-library reconcile, and include the result in that library's summary.
- `managed_titles` is the set of titles the reconcile pass actually handled, which the reconcilers already know.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_leftovers.py` covering: a prior-tool collection whose title we manage is not reported (it was adopted or is a conflict, either way it is accounted for); one whose title we do not manage **is** reported; an unlabelled collection is never reported regardless of title; the result is sorted; and an empty `adopt_from` reports nothing.

Use the fake collection from `tests/test_collection_adoption.py` rather than a new one.

- [ ] **Step 2: Run test to verify it fails**

Run: `rtk proxy python -m pytest tests/test_collection_leftovers.py -v`
Expected: FAIL with `ImportError: cannot import name 'unmanaged_prior_collections'`

- [ ] **Step 3: Implement, run the suite, ruff and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add src/autoposter/collections tests/test_collection_leftovers.py
git commit --no-gpg-sign -m "Report prior-tool collections this service does not manage"
```

---

## Task 3: Documenting the cutover

**Files:**
- Modify: `deploy/README.md`

**Notes for the implementer:**

Extend the cutover section written in the previous phase with the collections half. It must say, in order:

1. **Stop the previous tool first.** If both run with adoption enabled, both claim the same collections and fight over their contents on every pass. Adoption is a cutover step, not something to enable while the old tool is still scheduled.
2. Run the reconcile with `apply_to_plex: false` and read the report. With `adopt: true` it names every collection it would claim.
3. Then set `apply_to_plex: true` and run again.
4. What actually happens to a claimed collection: it gains the `autoposter` label, keeps the prior tool's label unless `adopt_removes_prior_label` is set, and is thereafter reconciled normally — the smart collections have their filters rewritten, the list collections have their membership diffed.
5. What is *not* claimed: collections with no label at all, which includes the operator's hand-made ones and Plex's own franchise collections; collections carrying some other tool's label; and any collection whose title this service does not manage.
6. The leftovers report, and that `Ratings Collections` — the previous tool's blank section separator — is expected to appear in it, because this service does not implement separators.

Give the concrete numbers for this library so the expected output is recognisable: **49 collections carry the `Kometa` label; 47 have titles this service manages and would be claimed; 2 are `Ratings Collections` separators and would be reported as left behind.**

- [ ] **Step 1: Write the documentation, run the suite and commit**

```bash
rtk proxy python -m pytest tests/ -v
rtk proxy ruff check src tests
git add deploy/README.md
git commit --no-gpg-sign -m "Document taking over the previous tool's collections"
```

---

## Deferred

- **Separator collections.** `Ratings Collections` is a permanently-blank placeholder acting as a visual divider in Plex's alphabetised list. Implementing it would let this service manage those two as well.
- **Collection posters.** The previous tool downloads a static image per collection; adopted collections keep whatever poster they already have.
- **Arr sync.** Still blocked on outbound Radarr and Sonarr credentials.
