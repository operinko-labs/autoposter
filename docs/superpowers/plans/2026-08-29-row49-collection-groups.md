# Collection Grouping + Separators (row 49) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generalise the one hand-built "Ratings Collections" divider into a declarative grouping scheme — every managed collection derives a group, every active group gets its own blank separator, and every managed collection gets the group's `!<NNN>_` sort-title prefix so Plex's collections tab shows the library in labelled blocks.

**Architecture:** One new pure module (`collections/groups.py`) owns the whole derivation: collection → group → section number → sort title, plus the per-group separator's title/summary/sort title. It imports nothing from the package at module scope, so every reconciler can import it without a cycle. The engine resolves each definition's group ONCE per library and hands the resulting prefix down as `sort_prefix=`; each reconciler, at the point it knows a concrete collection title, wraps its `settings` object in a read-only view carrying the derived sort title — so the value reaches `lists._settings_parts` (which is what makes the hash change and the write actually happen) without any `CollectionDefinition` ever being constructed or mutated. The separator reconcile moves out of `reconcile_content_ratings` and becomes a spec-driven routine the engine drives once per active group.

**Tech Stack:** Python 3.12, pydantic v2 (`config/schema.py`), SQLAlchemy 2 async (`db/models.py`), plexapi 4.18.2, pytest + pytest-asyncio, Docker Compose for the suite.

## Global Constraints

Every task's requirements implicitly include this section. These are law.

- **The derived value enters out-of-band, engine-side.** Never by constructing or mutating a `CollectionDefinition` (`model_copy(update=...)` sets `model_fields_set`, and `engine._completed` reads that as operator-explicit — the placeholder's value would then be inherited by every expanded unit). Never inside `reconcile._apply_sort_title` (the value must reach `lists._settings_parts` or the hash is unchanged and no re-write is ever triggered).
- **Explicit `definition.sort_title` always wins.** Three sources, in the order `engine._summary_for` already uses for summaries: the one written in the config, then the group-derived one, then `None`.
- **`SEPARATOR_HASH`'s current value is reproduced byte-identically** by the new function, pinned by a test. The shipped digest is `e22a14288c7962ea13a31e307aedf7e1ca204f6bf93238d65d0e436dcd5a4fb5` over `"Ratings Collections"`, `"Section separator for Ratings Collections."`, `"!110_!Ratings Collections"`.
- **The golden gate stays byte-identical except for the adjudicated cells**, amended in its own reviewed commit behind a diff gate. See the CONTRADICTION FLAG below — this phase needs TWO such commits, each gated to one class of cell.
- **Zero changes to `providers/`, `collections/facts*`, or the database beyond nothing.** A parallel phase (prefetch-A) owns those files. This phase needs **NO migration and NO new tables**: `ManagedCollection.kind` is a plain `String(16)` (`db/models.py:331`) and `"separator"` is already a value it carries.
- **Refusals RETURN, never raise.** A refusal is an action string in the pass's list, the shape `resolve_collision` and `reconcile_content_ratings`'s `PlexSearchUnavailable` branch already use.
- **No URLs and no tokens in any action string, log message, report or committed file.** Class names only out of `except` blocks. Probe output is scrubbed to `<plex-host>` before it is written anywhere.
- **Container discipline.** Unique compose projects `p49t<N>` (one per task). Copy `D:\Sites\autoposter\.superpowers\isolated-db.yml` into the worktree first, by absolute path. Tee every run to the **worktree's** `/app/.superpowers/`. No `--rm` on long runs — start detached and `docker wait` in the foreground. Teardown is `docker compose -p p49t<N> down` and **never** `-v`.
- **Mutation proofs are backup + `cmp`.** Before a mutating step on a fixture, copy the file; after, `cmp` the copy against the new one and read the diff.
- **Conventional commits, `--no-gpg-sign`, no attribution trailers of any kind.**
- **Artifacts are `p49-` prefixed** and written to the MAIN tree's `.superpowers/sdd/` by absolute path (`D:\Sites\autoposter\.superpowers\sdd\`), never to the worktree.
- **Branch `feat/collection-groups`, cut from `origin/main`.** Implementers run in worktrees (Step 1 of Task 1 sets this up).

---

## CONTRADICTION FLAG

**The golden fixture cannot change in `sort_title` cells alone.**

The controller's C5 says the golden fixture's `sort_title` cells amend "in their own reviewed commit … diff-gated", and the phase constraint restates that as "a diff gate proving only sort_title cells moved". C4, in the same document, says the one separator becomes N by design. Those two cannot both hold: the golden scenarios build the charts family and the awards family alongside the Common Sense family, so generalising separators necessarily **adds** `Chart Collections` and `Award Collections` to `movies_apply`/`shows_apply` (new action strings and new `state` entries), and **moves** the existing `created 'Ratings Collections'` line out of the Common Sense block to the end of the pass, where the separator routine now runs.

**Planned as adjudicated, split so each commit is provable:**

- **Task 3's amendment** (`fix(collections)`-adjacent, its own commit): separator rows only. Gate: every changed line is either a separator action string, a separator `state` object, or the Ratings separator's own `sort_title` cell.
- **Task 4's amendment** (its own commit): **only `"sort_title":` lines change.** This is the gate C5 asks for, verbatim, and Task 4 meets it exactly.

Both commits are reviewed, both carry the re-capture command in the message, and the fixture's module docstring gains a paragraph for each — the shape `tests/test_builder_port_golden.py`'s existing "One deliberate amendment, phase 10a-2" paragraph already set.

**Second, smaller flag.** C1 says operator `definitions:` entries fall into a final "Collections" group. Kometa's transcribed separator formula is `<<key_name>> Collections` (`docs/research/kometa-collections.md:429`), which would render that group's separator as `"Collections Collections"`. Planned resolution: the operator group's key is `operator` and its separator title is the un-suffixed `"Collections"` — a deliberate, single, commented exception to the formula, with its summary still following the formula (`"Section separator for Collections."`).

---

## The scheme, pinned (NOT_KOMETA)

Ours, said plainly. We do **not** transcribe Kometa's per-file `collection_section` numbers — only four are on record and fetching ~40 defaults files for cosmetic arithmetic fails the value test. Where adopted collections carry Kometa's prefixes, our derived values replace them on collections this service manages; collections it does not manage are untouched. That replacement IS the migration.

Canonical order and section numbers (position × 10, zero-padded to three):

| # | group key | section | separator title | member sort title |
|---|-----------|---------|-----------------|-------------------|
| 1 | `charts` | `010` | `Chart Collections` | `!010_<ord>_IMDb Top 250` |
| 2 | `awards` | `020` | `Award Collections` | `!020_<ord>_Oscars Best Picture Winners` |
| 3 | `content_ratings` | `030` | `Ratings Collections` | `!030_<ord>_Age 13+ Movies` |
| 4 | `content` | `040` | `Content Collections` | `!040_<title>` |
| 5 | `location` | `050` | `Location Collections` | `!050_<title>` |
| 6 | `media` | `060` | `Media Collections` | `!060_<title>` |
| 7 | `people` | `070` | `People Collections` | `!070_<title>` |
| 8 | `production` | `080` | `Production Collections` | `!080_<title>` |
| 9 | `time` | `090` | `Time Collections` | `!090_<title>` |
| 10 | `operator` | `100` | `Collections` | `!100_<title>` |

**LAW Addendum 1/2 (2026-08-29):** rows 1-3 carry an `<ord>` slot — a
per-member ordering key filled for families that supply a natural order (CS
buckets by ascending age index, zero-padded; award years descending, inverted
so newest files first, matching Kometa's hand-order intent; charts in
`CHART_COLLECTIONS`' canonical order). Rows 4-10 have no natural order and
keep the two-part `!<NNN>_<title>` shape (alphabetical within group). The `~`
leftovers sentinel (Kometa's Not-Rated trick) is adopted for other/leftover
buckets within a group. The keys' justification is collation-independence
(an explicit key makes within-block order ours regardless of Plex's own
collation, which T1's probe proved differs from Python's) plus the measured
Oscars hand-order regression (`docs/research/collection-sort-probe/README.md`
§3a) — not the Common Sense "Age 2+ after Age 18+" claim, which was an
unlabelled Python-collation inference the same probe's capture contradicts
for leading digit runs and leaves unverified mid-string.

Formulas, both on record:

- **Separator naming** — `docs/research/kometa-collections.md:429`, transcribed: title `<<key_name>> Collections`, summary `Section separator for <<key_name>> Collections.`
- **Sort title** — `.superpowers/sdd/p-prefetch-upstream.md:386-388`: `sort_title: "!<<collection_section>><<pre>><<order_<<key>>>><<title>>"` with `pre: "_"` and `order_<<key>>: ""`, i.e. `!<section>_<title>`. That is upstream's own rendering (an empty order in Kometa's defaults file), transcribed, not ours — it is our *fallback* case (rows 4-10 above) when no ordering key applies. The separator template (`collections/reconcile.py:36-44`) is the same with an extra `!` before the title, which is the float trick that puts a divider above its block.

The nine group keys 1-9 are exactly `catalog.CATEGORIES`' keys (`collections/catalog.py:69-79`); `operator` is the tenth and is ours.

---

## File Structure

**Created:**

- `src/autoposter/collections/groups.py` — the whole derivation, pure and I/O-free. Canonical order, effective order under `group_order`, section numbers, sort prefixes, the settings view that carries a derived sort title, separator specs, and separator title enumeration. **Module-scope imports: `dataclasses` only** — every reference to `catalog` or `imdb_award` is a local import inside the one function that needs it, because `catalog` imports `config.schema` and `builders/__init__` imports `cs_bucket` which imports `reconcile`; a module-scope import here would close that ring.
- `tests/test_collection_groups.py` — the derivation's own tests.
- `tests/test_collection_group_separators.py` — the N-separator lifecycle, the ops/blank collision, the sweep behaviour.
- `docs/research/collection-sort-probe/README.md` — Task 1's evidence, scrubbed.

**Modified:**

- `src/autoposter/collections/reconcile.py` — `separator_hash()` replaces the `SEPARATOR_HASH` constant; `SEPARATOR_SORT_TITLE` is deleted (derived now); `_reconcile_separator` becomes the public, spec-driven `reconcile_separator`; `reconcile_content_ratings` loses its `separators` parameter and gains `sort_prefix`.
- `src/autoposter/collections/lists.py` — `reconcile_list_collection` gains `sort_prefix`.
- `src/autoposter/collections/smart.py` — `reconcile_smart_collection` gains `sort_prefix`.
- `src/autoposter/collections/engine.py` — resolves the group per definition, passes `sort_prefix` down, drives the separator pass, and counts separator titles in `definition_titles`.
- `src/autoposter/collections/builders/base.py` — `SmartContext` gains `sort_prefix`.
- `src/autoposter/collections/builders/cs_bucket.py` — forwards `sort_prefix`, drops `separators`, drops the separator title from `titles()`.
- `src/autoposter/collections/builders/smart_filter.py`, `src/autoposter/collections/builders/dynamic.py` — forward `sort_prefix`.
- `src/autoposter/config/schema.py` — `collections.group_order`, its validator, and `separators`' new docstring.
- `tests/test_collection_separator.py` — call sites only (row 191's forked double stays forked; do not consolidate).
- `tests/fixtures/collections/golden_port.json` + `tests/test_builder_port_golden.py` — two adjudicated amendments.
- `deploy/README.md`, `config/autoposter.example.yaml`, `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` — Task 5.

**Explicitly NOT touched:** anything under `src/autoposter/providers/`, `src/autoposter/collections/facts*`, `alembic/`, `src/autoposter/db/models.py`. The `visible_*`/`hub_priority` machinery is a different Plex object (hubs, Plex Pass) and is out of scope.

---

### Task 1: The live probes, and the scheme pinned

**Files:**
- Create: `docs/research/collection-sort-probe/README.md`
- Create: `docs/research/collection-sort-probe/probe-output.txt`
- Create: `D:\Sites\autoposter\.superpowers\sdd\p49-task-1-report.md`

**Interfaces:**
- Consumes: nothing.
- Produces: the measured facts Task 2 and Task 3 cite — (a) what `titleSort` the adopted chart/award collections actually carry on the live server, (b) whether Plex's collections tab orders by `titleSort`, (c) which `separators/orig/<key>.jpg` files exist in `Kometa-Team/Default-Images`, which becomes `groups.SEPARATOR_POSTER_KEYS`.

- [ ] **Step 1: Cut the branch and the worktree**

From the main tree at `D:\Sites\autoposter`:

```bash
git fetch origin
git worktree add -b feat/collection-groups D:/Sites/autoposter-p49 origin/main
cp D:/Sites/autoposter/.superpowers/isolated-db.yml D:/Sites/autoposter-p49/.superpowers/isolated-db.yml
```

Every later step runs inside `D:/Sites/autoposter-p49`. Reports are still written to `D:\Sites\autoposter\.superpowers\sdd\` by absolute path.

Verify: `cd D:/Sites/autoposter-p49 && git status` shows `On branch feat/collection-groups`, clean, and `git log --oneline -1` matches `origin/main`.

- [ ] **Step 2: Write the probe script**

The script contains no literal URL and no literal token; both arrive from the environment for the duration of the run. Every printed line goes through `scrub`. `except` blocks print the class name only — a plexapi failure's own message quotes the tokenised URL. This is the shape `docs/research/plex-dynamic-probe/README.md:246-291` established; follow it exactly.

Write `D:/Sites/autoposter-p49/probe_sort.py` (a scratch file, deleted in Step 6 — it is reproduced verbatim in the README instead):

```python
"""Row 49 Task 1: three read-only measurements. Nothing is written to Plex."""
import os
from urllib.parse import urlsplit

import httpx
from plexapi.server import PlexServer

# The candidate separator artwork stems, one per group in the canonical order.
# "content_rating" is the one already on record -- ``posters.hosted_poster_url``
# builds ``separators/orig/content_rating.jpg`` for the shipped divider today.
CANDIDATE_KEYS = [
    "chart", "award", "content_rating", "content", "location",
    "media", "people", "production", "time", "collectionless",
    "genre", "decade", "year", "studio", "country", "franchise",
]
DEFAULT_IMAGES_BASE = (
    "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"
)


def scrub(text: str) -> str:
    out = str(text)
    url = os.environ["PROBE_PLEX_URL"]
    for secret in [url, url.rstrip("/"), urlsplit(url).netloc,
                   os.environ["PROBE_PLEX_TOKEN"]]:
        if secret:
            out = out.replace(secret, "<plex-host>")
    return out


def probe_plex() -> None:
    try:
        server = PlexServer(os.environ["PROBE_PLEX_URL"],
                            os.environ["PROBE_PLEX_TOKEN"])
        sections = list(server.library.sections())
    except Exception as error:  # class name only, never the message
        print("UNREACHABLE (%s) -- Plex probes BLOCKED" % type(error).__name__)
        return

    for section in sections:
        if section.type not in ("movie", "show"):
            continue
        try:
            collections = list(section.collections())
        except Exception as error:
            print(scrub("=== section %s: REFUSED (%s)"
                        % (section.title, type(error).__name__)))
            continue

        print(scrub("=== section %s (%s): %d collection(s)"
                    % (section.title, section.type, len(collections))))

        # (a) what sort titles the collections we would manage carry TODAY.
        print("--- titleSort of every collection carrying a non-empty one")
        for collection in collections:
            sort = getattr(collection, "titleSort", None) or ""
            if sort:
                print(scrub("    %-44s %r" % (collection.title, sort)))

        # (b) does the tab honour titleSort? The listing order Plex returns
        # with no sort argument is the tab's own. Compare it against the two
        # candidate orderings. A "!"-prefixed separator sorts first under
        # titleSort and under R under title, so the two orders differ and the
        # comparison is decisive rather than a coincidence.
        listed = [c.title for c in collections]
        by_title = sorted(listed, key=lambda t: t.lower())
        by_sort = [
            c.title for c in sorted(
                collections,
                key=lambda c: (getattr(c, "titleSort", None) or c.title).lower(),
            )
        ]
        print("--- tab ordering")
        print("    default listing == sorted by title      : %s"
              % (listed == by_title))
        print("    default listing == sorted by titleSort  : %s"
              % (listed == by_sort))
        print("    the two candidate orders differ         : %s"
              % (by_title != by_sort))
        print(scrub("    first 5 as listed: %s" % ", ".join(listed[:5])))


def probe_images() -> None:
    # (c) which separator artwork stems exist. No credential, no Plex, and the
    # URL is built from a constant -- nothing here needs scrubbing, and nothing
    # here is printed from an exception's message either.
    print("=== Default-Images separators/orig")
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for key in CANDIDATE_KEYS:
            url = "%s/separators/orig/%s.jpg" % (DEFAULT_IMAGES_BASE, key)
            try:
                response = client.head(url)
            except Exception as error:
                print("    %-18s ERROR (%s)" % (key, type(error).__name__))
                continue
            print("    %-18s %d" % (key, response.status_code))


if __name__ == "__main__":
    probe_plex()
    probe_images()
```

- [ ] **Step 3: Run the probe, read-only, credentials never on a command line**

Export the two variables into the shell from `.env`'s `AUTOPOSTER_PLEX_TOKEN` and the operator's Plex URL, then pass them as **bare `-e NAME`** so neither value appears in any argv:

```bash
cd D:/Sites/autoposter-p49
docker compose -p p49t1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm --no-deps -e PROBE_PLEX_URL -e PROBE_PLEX_TOKEN \
    test python /app/probe_sort.py 2>&1 \
    | tee .superpowers/p49-probe.log
docker compose -p p49t1 down
```

Expected: a `=== section Movies (movie): N collection(s)` block per library, a `titleSort` listing, the three-line tab-ordering verdict, and a status code per candidate image key. If the first line is `UNREACHABLE (...)`, the Plex half is BLOCKED — record that verbatim, do not retry with a different credential, and carry the block into the report. The image half still runs.

- [ ] **Step 4: Verify the output carries no secret**

```bash
grep -ciE "$(echo $PROBE_PLEX_TOKEN)" .superpowers/p49-probe.log
grep -ciE "$(urlsplit-host)" .superpowers/p49-probe.log   # the bare hostname
```

Expected: `0` for both. If either is non-zero the scrub failed — fix `scrub` and re-run; do NOT hand-edit the log.

- [ ] **Step 5: Write the evidence file**

`docs/research/collection-sort-probe/README.md`, following `docs/research/plex-dynamic-probe/README.md`'s structure:

1. **Header** — what this is (row 49 Task 1), the date, "read-only throughout", the scrub placeholder `<plex-host>`, and the statement that the script holds no literal URL and no literal token.
2. **What it exists to decide** — three questions, each with the decision it feeds: (a) the unrecorded prefix adopted collections carry, so the migration's "our values replace them" claim is measured rather than assumed; (b) whether the collections tab honours `titleSort` at all, which the recon called "very likely but not recorded" — if it does not, the whole scheme is cosmetic and the phase must say so in the PR body; (c) which separator artwork stems exist, which fixes `SEPARATOR_POSTER_KEYS`.
3. **Results** — the scrubbed output, verbatim, in a fenced block, plus one paragraph per question stating the answer and, where a question could not be answered, saying BLOCKED in those words.
4. **The script, verbatim**, in a fenced block.
5. **How it was run** — the exact command from Step 3.

Copy the scrubbed log to `docs/research/collection-sort-probe/probe-output.txt`.

- [ ] **Step 6: Pin the scheme in the report**

Write `D:\Sites\autoposter\.superpowers\sdd\p49-task-1-report.md` containing:

- The three probe answers, each marked MEASURED or BLOCKED.
- The full canonical-order table from this plan's "The scheme, pinned" section, marked **NOT_KOMETA**, with the sentence: *"These section numbers are ours. Kometa's own per-file `collection_section` values are not transcribed — only four are on record, and fetching ~40 defaults files for cosmetic arithmetic fails the value test. Where an adopted collection carries a Kometa prefix, our value replaces it on collections this service manages, and that replacement is the migration."*
- The two formula citations, verbatim, with their line numbers: `docs/research/kometa-collections.md:429` and `.superpowers/sdd/p-prefetch-upstream.md:386-388`.
- `SEPARATOR_POSTER_KEYS` as measured: every candidate key that returned `200`, mapped to its group; every group whose key returned `404` recorded as **no poster**, which is graceful by design (`posters.hosted_poster_url` already returns `None` for an unrecognised kind, and `apply_poster` treats `None` as "leave the poster alone").

Delete the scratch script and log:

```bash
rm probe_sort.py .superpowers/p49-probe.log
```

- [ ] **Step 7: Commit**

```bash
git add docs/research/collection-sort-probe
git commit --no-gpg-sign -m "docs(collections): the row-49 sort-title and separator-artwork probes"
```

Verify: `git show --stat HEAD` lists exactly the two files under `docs/research/collection-sort-probe/`, and `git status` is clean.

---

### Task 2: The groups module — pure derivation and the one config field

**Files:**
- Create: `src/autoposter/collections/groups.py`
- Create: `tests/test_collection_groups.py`
- Modify: `src/autoposter/config/schema.py:633-715` (the `CollectionsConfig` fields) and its validator block

**Interfaces:**
- Consumes: Task 1's `SEPARATOR_POSTER_KEYS` measurement.
- Produces, and Tasks 3 and 4 rely on these exact names and types:
  - `CANONICAL_ORDER: tuple[str, ...]` — the ten keys, in order.
  - `OPERATOR_GROUP: str` = `"operator"`.
  - `SEPARATOR_POSTER_KEYS: dict[str, str]` — group key → `Default-Images` stem; a group absent from it gets no poster.
  - `effective_order(config) -> tuple[str, ...]`
  - `section_number(group: str, order: tuple[str, ...]) -> str` — `"010"` … `"100"`.
  - `sort_prefix(group: str, order: tuple[str, ...]) -> str` — `"!030_"`.
  - `separator_title(group: str) -> str`
  - `separator_summary(group: str) -> str`
  - `separator_sort_title(group: str, order: tuple[str, ...]) -> str`
  - `preset_groups(config, library_type: str) -> dict[str, str]` — collection title → group, for the presets switched on.
  - `builtin_group(builder: str) -> str | None`
  - `group_for(definition, index: dict[str, str]) -> str`
  - `sort_prefix_for(definition, index: dict[str, str], order: tuple[str, ...]) -> str`
  - `with_derived_sort_title(settings, prefix: str | None, title: str)` — returns `settings` unchanged, or a read-only view of it whose `sort_title` is `prefix + title`.
  - `@dataclass(frozen=True) class SeparatorSpec` with fields `group: str`, `title: str`, `summary: str`, `sort_title: str`, `poster_key: str | None`.
  - `separator_groups(definitions, library_type: str, config) -> list[str]`
  - `separator_specs(definitions, library_type: str, config) -> list[SeparatorSpec]`
  - `separator_titles(definitions, library_type: str, config) -> set[str]`
  - New config field `collections.group_order: list[str] | None = None`.

- [ ] **Step 1: Write the failing tests for the derivation**

Create `tests/test_collection_groups.py`:

```python
"""Row 49: collection -> group -> section number -> sort title, all pure."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from autoposter.collections import groups
from autoposter.config.schema import CollectionDefinition, CollectionsConfig


def config(**kwargs):
    return SimpleNamespace(collections=CollectionsConfig(**kwargs))


def test_canonical_order_is_the_nine_catalog_categories_plus_operator():
    from autoposter.collections.catalog import CATEGORIES

    assert groups.CANONICAL_ORDER[-1] == groups.OPERATOR_GROUP
    assert set(groups.CANONICAL_ORDER[:-1]) == set(CATEGORIES)
    assert groups.CANONICAL_ORDER == (
        "charts", "awards", "content_ratings", "content", "location",
        "media", "people", "production", "time", "operator",
    )


def test_section_numbers_are_position_times_ten():
    order = groups.CANONICAL_ORDER
    assert groups.section_number("charts", order) == "010"
    assert groups.section_number("content_ratings", order) == "030"
    assert groups.section_number("operator", order) == "100"


def test_sort_prefix_and_member_sort_title():
    order = groups.CANONICAL_ORDER
    assert groups.sort_prefix("content_ratings", order) == "!030_"
    assert (
        groups.sort_prefix("content_ratings", order) + "Age 13+ Movies"
        == "!030_Age 13+ Movies"
    )


def test_separator_naming_follows_the_transcribed_kometa_formula():
    order = groups.CANONICAL_ORDER
    # docs/research/kometa-collections.md:429 -- "<<key_name>> Collections" /
    # "Section separator for <<key_name>> Collections."
    assert groups.separator_title("content_ratings") == "Ratings Collections"
    assert (
        groups.separator_summary("content_ratings")
        == "Section separator for Ratings Collections."
    )
    assert groups.separator_title("charts") == "Chart Collections"
    assert groups.separator_sort_title("charts", order) == "!010_!Chart Collections"


def test_the_operator_group_is_not_called_collections_collections():
    assert groups.separator_title(groups.OPERATOR_GROUP) == "Collections"
    assert (
        groups.separator_summary(groups.OPERATOR_GROUP)
        == "Section separator for Collections."
    )


def test_group_order_reorders_and_renumbers():
    order = groups.effective_order(config(group_order=["awards", "charts"]))
    assert order[:2] == ("awards", "charts")
    assert groups.section_number("awards", order) == "010"
    assert groups.section_number("charts", order) == "020"
    # Unnamed groups keep the canonical order behind the named ones.
    assert order[2:] == groups.CANONICAL_ORDER[2:]
    assert sorted(order) == sorted(groups.CANONICAL_ORDER)


def test_no_group_order_is_the_canonical_order():
    assert groups.effective_order(config()) == groups.CANONICAL_ORDER


def test_group_order_refuses_an_unknown_name_and_lists_the_valid_set():
    with pytest.raises(ValidationError) as caught:
        CollectionsConfig(group_order=["chartz"])
    message = str(caught.value)
    assert "chartz" in message
    assert "content_ratings" in message


def test_group_order_refuses_a_repeated_name():
    with pytest.raises(ValidationError) as caught:
        CollectionsConfig(group_order=["charts", "charts"])
    assert "twice" in str(caught.value)


def test_builtins_are_grouped_by_builder():
    assert groups.builtin_group("imdb_chart") == "charts"
    assert groups.builtin_group("imdb_award") == "awards"
    assert groups.builtin_group("cs_bucket") == "content_ratings"
    assert groups.builtin_group("plex_search") is None


def test_every_ceremony_year_builder_is_an_award():
    from autoposter.collections.builders.imdb_award import EVENTS

    for event in EVENTS.values():
        assert groups.builtin_group(event.years_builder) == "awards"


def test_builtin_groups_agree_with_the_catalogs_setting_rows():
    # The three SETTING_PRESETS rows are the catalog's own statement of which
    # category each shipped family belongs to; the builder table must not drift
    # from them (collections/catalog.py:546-589).
    from autoposter.collections.catalog import SETTING_PRESETS

    by_key = {preset.key: preset.category for preset in SETTING_PRESETS}
    assert groups.builtin_group("imdb_chart") == by_key["imdb_charts"]
    assert groups.builtin_group("imdb_award") == by_key["oscars"]
    assert groups.builtin_group("cs_bucket") == by_key["content_ratings_divider"]


def test_a_preset_definition_takes_its_presets_category():
    index = groups.preset_groups(config(presets=["chart_tracearr_movies"]), "Movie")
    assert index
    assert set(index.values()) == {"charts"}


def test_an_operator_definition_falls_into_the_operator_group():
    definition = CollectionDefinition(title="Hand Picked", builder="plex_all")
    assert groups.group_for(definition, {}) == groups.OPERATOR_GROUP


def test_a_preset_title_outranks_the_builder_table():
    definition = CollectionDefinition(title="Ours", builder="imdb_chart")
    assert groups.group_for(definition, {"Ours": "people"}) == "people"


def test_separator_specs_cover_the_groups_present_in_order():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
        CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                             params={"chart": "top"}),
    ]
    specs = groups.separator_specs(definitions, "Movie", config())
    assert [spec.group for spec in specs] == ["charts", "content_ratings"]
    assert [spec.title for spec in specs] == [
        "Chart Collections", "Ratings Collections",
    ]
    assert specs[1].sort_title == "!030_!Ratings Collections"


def test_separators_false_yields_no_specs_and_no_titles():
    definitions = [
        CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket"),
    ]
    off = config(separators=False)
    assert groups.separator_specs(definitions, "Movie", off) == []
    assert groups.separator_titles(definitions, "Movie", off) == set()


def test_an_explicit_sort_title_is_never_replaced():
    definition = CollectionDefinition(
        title="Hand Picked", builder="plex_all", sort_title="!999_mine"
    )
    view = groups.with_derived_sort_title(definition, "!100_", "Hand Picked")
    assert view is definition
    assert view.sort_title == "!999_mine"


def test_the_derived_view_reads_through_to_the_definition():
    definition = CollectionDefinition(
        title="Hand Picked", builder="plex_all", labels=["x"], collection_mode="hide"
    )
    view = groups.with_derived_sort_title(definition, "!100_", "Hand Picked")
    assert view.sort_title == "!100_Hand Picked"
    assert view.labels == ["x"]
    assert view.collection_mode == "hide"
    assert view.title == "Hand Picked"
    # Out of band: the definition itself is untouched, and nothing about it
    # reads as operator-set -- which is what engine._completed checks.
    assert definition.sort_title is None
    assert "sort_title" not in definition.model_fields_set


def test_no_prefix_and_no_settings_are_both_pass_through():
    definition = CollectionDefinition(title="Hand Picked", builder="plex_all")
    assert groups.with_derived_sort_title(definition, None, "Hand Picked") is definition
    assert groups.with_derived_sort_title(None, "!100_", "Hand Picked") is None


def test_the_derived_view_reaches_the_settings_hash():
    from autoposter.collections.lists import _settings_parts

    definition = CollectionDefinition(title="Hand Picked", builder="plex_all")
    assert _settings_parts(definition) == []
    view = groups.with_derived_sort_title(definition, "!100_", "Hand Picked")
    assert _settings_parts(view) == ["sort_title='!100_Hand Picked'"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd D:/Sites/autoposter-p49
docker compose -p p49t2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_collection_groups.py 2>&1 \
    | tee .superpowers/p49-t2-red.log
```

Expected: a collection error, `ModuleNotFoundError: No module named 'autoposter.collections.groups'`.

- [ ] **Step 3: Write the groups module**

Create `src/autoposter/collections/groups.py`:

```python
"""Which block of the collections tab each managed collection belongs to.

Roadmap row 49. Plex's collections tab is one alphabetical list, so a library
with forty managed collections shows them interleaved with the operator's own
and with Plex's franchise ones. Kometa's answer -- and now ours -- is a sort
title that overrides the alphabet: a ``!<section>_`` prefix puts every
collection of one family together, and a blank "separator" collection with an
extra ``!`` floats above the family as its heading.

Everything here is PURE. No Plex, no database, no clock, no network -- it is
called from ``config/schema.py``'s title-collision validator (through
``engine.definition_titles``) while a settings save is being validated, and a
validator that reached the network would turn a config write into an outage.

**Nothing is imported from this package at module scope, deliberately.** The
two tables that need the catalog and the award registry take a local import
inside the one function that reads them. ``catalog`` imports ``config.schema``,
and ``builders/__init__`` imports ``cs_bucket`` which imports ``reconcile`` --
so a module-scope import here would close a ring through every reconciler that
imports this module.

**The section numbers are OURS (NOT_KOMETA).** Kometa assigns a
``collection_section`` per defaults file; only four of those are on record here,
and fetching some forty files to transcribe cosmetic arithmetic fails the value
test this repository applies to every other borrowed table. So the numbers below
are spaced tens in our own canonical order, and where an adopted collection
carries a Kometa prefix, ours replaces it on collections this service manages.
Collections it does not manage are never touched. **The per-member ordering
key is OURS too (NOT_KOMETA, LAW Addendum 1)** -- filled for families that
supply a natural order (CS buckets ascending, award years descending, charts
in ``CHART_COLLECTIONS`` order) and otherwise absent, on grounds of
collation-independence and the measured Oscars hand-order regression, not the
unverified Common Sense collation claim an earlier draft rested on.

The two FORMULAS, by contrast, are transcribed and cited:

- the separator's name and summary -- ``<<key_name>> Collections`` and
  ``Section separator for <<key_name>> Collections.`` --
  ``docs/research/kometa-collections.md:429``;
- the sort title -- ``!<<collection_section>><<pre>><<order>><<title>>`` with
  ``pre: "_"`` and an empty order, i.e. ``!<section>_<title>`` --
  ``.superpowers/sdd/p-prefetch-upstream.md:386-388``. That empty-order
  rendering is upstream's own and is our fallback case when no ordering key
  applies; see above for the filled case. The separator template
  (``collections/reconcile.py:36-44``) is the same with an extra ``!``.
"""
from dataclasses import dataclass

# The ten groups, in the order the tab shows them. The first nine are exactly
# ``catalog.CATEGORIES``' keys -- Kometa's own defaults taxonomy, which is what
# an operator arriving from Kometa is looking for, and which the collections
# picker already sorts its tabs by. The tenth is ours: an operator's own
# ``definitions:`` entry belongs to no Kometa category, and putting it last is
# the honest place for "everything this table did not name".
CANONICAL_ORDER: tuple[str, ...] = (
    "charts",
    "awards",
    "content_ratings",
    "content",
    "location",
    "media",
    "people",
    "production",
    "time",
    "operator",
)

OPERATOR_GROUP = "operator"

# What goes in the ``<<key_name>>`` slot of the transcribed separator formula.
# Singular, because the formula appends "Collections": "Chart Collections", not
# "Charts Collections". ``content_ratings`` maps to "Ratings" because that is
# what the shipped divider is already called on every live server this service
# has run against ("Ratings Collections"), and renaming a live collection is a
# migration nobody asked for.
_KEY_NAMES: dict[str, str] = {
    "charts": "Chart",
    "awards": "Award",
    "content_ratings": "Ratings",
    "content": "Content",
    "location": "Location",
    "media": "Media",
    "people": "People",
    "production": "Production",
    "time": "Time",
}

# The one deliberate exception to the formula. The operator group's key name
# would be "Collections", and the formula would render "Collections
# Collections". The title drops the redundant half; the SUMMARY still follows
# the formula, because there the doubling never occurs.
_OPERATOR_TITLE = "Collections"

# Which group a built-in family belongs to, keyed by builder. Only the shipped
# families are here -- the three the catalog's own ``SETTING_PRESETS`` rows
# describe (``collections/catalog.py:546-589``), whose categories these three
# values are pinned against by ``tests/test_collection_groups.py``. Every
# ceremony's year-collections builder is handled by the suffix rule in
# ``builtin_group`` rather than by sixteen rows copied out of
# ``imdb_award.EVENTS``: a second copy of that registry is exactly the drift
# this codebase refuses everywhere else.
_BUILTIN_GROUPS: dict[str, str] = {
    "imdb_chart": "charts",
    "imdb_award": "awards",
    "cs_bucket": "content_ratings",
}

_AWARD_YEARS_SUFFIX = "_award_years"

# The ``Default-Images`` separator artwork stem for each group, as MEASURED --
# see ``docs/research/collection-sort-probe/README.md``. A group absent from
# this table gets no poster, which is graceful by construction:
# ``posters.hosted_poster_url`` returns None for a key it cannot build a path
# for, and ``apply_poster`` reads None as "leave the poster alone" rather than
# guessing a URL that would 404 and leave the collection quietly bare.
SEPARATOR_POSTER_KEYS: dict[str, str] = {
    # FILLED FROM TASK 1'S MEASUREMENT. "content_rating" is the one already on
    # record -- it is the key ``reconcile._reconcile_separator`` passes today.
    "content_ratings": "content_rating",
}


@dataclass(frozen=True)
class SeparatorSpec:
    """One group's blank divider: everything needed to reconcile it.

    Frozen and value-only, so the reconciler takes a description rather than a
    module constant -- which is the whole of "generalise the one into N".
    """

    group: str
    title: str
    summary: str
    sort_title: str
    poster_key: str | None


def effective_order(config) -> tuple[str, ...]:
    """The canonical order, reordered by ``collections.group_order``.

    A partial list is allowed and is the expected use: the groups it names lead,
    in the order it names them, and every group it does not name follows in the
    canonical order. That keeps "put my own collections at the top" a one-line
    setting instead of a ten-name permutation an operator has to keep in sync
    with a table they cannot see.

    Section numbers derive from POSITION, so reordering renumbers -- which is a
    one-off re-write of every managed collection's sort title, exactly like the
    first pass after this feature ships. Said plainly in ``deploy/README.md``.
    """
    named = tuple(getattr(config.collections, "group_order", None) or ())
    return named + tuple(group for group in CANONICAL_ORDER if group not in named)


def section_number(group: str, order: tuple[str, ...]) -> str:
    """``"010"`` … ``"100"`` -- the group's 1-based position times ten.

    Spaced tens rather than 1..10 so a group inserted later can take a number
    between two shipped ones without renumbering the collections either side.
    """
    return "%03d" % ((order.index(group) + 1) * 10)


def sort_prefix(group: str, order: tuple[str, ...]) -> str:
    """``"!030_"`` -- what every member of the group's sort title starts with."""
    return "!%s_" % section_number(group, order)


def separator_title(group: str) -> str:
    if group == OPERATOR_GROUP:
        return _OPERATOR_TITLE
    return "%s Collections" % _KEY_NAMES[group]


def separator_summary(group: str) -> str:
    return "Section separator for %s." % separator_title(group)


def separator_sort_title(group: str, order: tuple[str, ...]) -> str:
    """The separator's own sort title: the members' prefix plus a second ``!``.

    That extra bang is the whole trick -- it sorts before every member of the
    same section, so the blank collection floats to the top of its block and
    reads as the block's heading.
    """
    return "%s!%s" % (sort_prefix(group, order), separator_title(group))


def builtin_group(builder: str) -> str | None:
    """The group a shipped family's builder belongs to, or None.

    The suffix rule covers all sixteen ceremonies at once. Every
    ``AwardEvent.years_builder`` is spelled ``<event>_award_years``
    (``builders/imdb_award.py:208-544``), and a test walks ``EVENTS`` to prove
    this answers "awards" for each of them -- so the registry stays the single
    source of ceremony names and this module holds none of them.
    """
    if builder in _BUILTIN_GROUPS:
        return _BUILTIN_GROUPS[builder]
    if builder.endswith(_AWARD_YEARS_SUFFIX):
        return "awards"
    return None


def preset_groups(config, library_type: str) -> dict[str, str]:
    """``{collection title: group}`` for the catalog presets switched on.

    The catalog already knows each preset's category, and a preset knows the
    definitions it expands to, so the index is the join of the two -- no new
    table, and a preset moved to another category moves its collections with it
    on the next pass rather than after a migration.

    An unknown key expands to nothing rather than raising, the same posture
    ``CollectionsConfig._presets_must_be_known_and_ready`` documents: a
    ``KeyError`` from inside a validator is a 500 on a settings save, and the
    refusal for a bad key belongs to that validator alone.

    Imported locally -- ``catalog`` imports ``config.schema``, and this module is
    imported by every reconciler; see the module docstring.
    """
    from autoposter.collections.catalog import BY_KEY

    index: dict[str, str] = {}
    for key in (getattr(config.collections, "presets", None) or []):
        preset = BY_KEY.get(key)
        if preset is None:
            continue
        for definition in preset.definitions(library_type):
            index[definition.title] = preset.category
    return index


def group_for(definition, index: dict[str, str]) -> str:
    """Which block this definition's collections belong to.

    Three sources, most specific first. A preset's own category wins, because
    the catalog said so explicitly. Then the builder table, which is what the
    shipped families are recognised by -- they come from ``sources.py`` and
    carry no preset key at all. Everything else is the operator's, and the
    operator group is last in the canonical order for that reason.
    """
    group = index.get(definition.title)
    if group is not None:
        return group
    return builtin_group(definition.builder) or OPERATOR_GROUP


def sort_prefix_for(definition, index: dict[str, str], order: tuple[str, ...]) -> str:
    return sort_prefix(group_for(definition, index), order)


class _DerivedSortTitle:
    """A definition, read through, with the group's sort title substituted.

    This is how the derived value gets in OUT OF BAND, and both halves of that
    matter. It is not written onto the definition, because ``model_copy`` marks
    the field in ``model_fields_set`` and ``engine._completed`` reads that as
    "the operator set this" -- a placeholder so marked would hand one sort title
    to every collection a family expands into, instead of each getting its own.
    And it is not applied inside ``reconcile._apply_sort_title`` either, because
    that function runs AFTER the hash short-circuit: a value that appeared only
    there would never change the stored hash, so an unchanged pass would skip
    the collection and the sort title would never be written at all. Wrapping
    the settings object puts the value in front of ``lists._settings_parts``,
    which is the one place that decides whether a pass has work to do.

    Read-only and total: every attribute but ``sort_title`` reads through to the
    definition, so this stands in wherever one does.
    """

    __slots__ = ("_definition", "sort_title")

    def __init__(self, definition, sort_title: str):
        object.__setattr__(self, "_definition", definition)
        object.__setattr__(self, "sort_title", sort_title)

    def __getattr__(self, name: str):
        return getattr(self._definition, name)

    def __repr__(self) -> str:
        return "<derived sort_title=%r on %r>" % (
            self.sort_title, getattr(self._definition, "title", None),
        )


def with_derived_sort_title(settings, prefix: str | None, title: str):
    """``settings``, with the group-derived sort title filled in if it is wanted.

    Returns the object unchanged in the three cases where nothing is derived:
    there is no definition, there is no group prefix, or the definition already
    names a sort title. The last is the precedence rule, and it is the same one
    ``engine._summary_for`` applies to summaries: an explicit choice in the
    config wins over anything derived, because a derived value that silently
    overrode it would be a setting that reads as applied and is not.
    """
    if settings is None or prefix is None:
        return settings
    if getattr(settings, "sort_title", None) is not None:
        return settings
    return _DerivedSortTitle(settings, prefix + title)


def separator_groups(definitions, library_type: str, config) -> list[str]:
    """The groups these definitions put collections in, in tab order.

    Empty when ``collections.separators`` is off -- that switch now governs
    every group's divider, not just the Common Sense one.

    Gated-off definitions count, deliberately, for ``definition_titles``'
    reason: a family outside its schedule this pass still has collections in
    the library, and a heading that vanished and came back would be a create
    and a delete every other pass.
    """
    if not getattr(config.collections, "separators", False):
        return []
    index = preset_groups(config, library_type)
    present = {group_for(definition, index) for definition in definitions}
    return [group for group in effective_order(config) if group in present]


def separator_specs(definitions, library_type: str, config) -> list[SeparatorSpec]:
    order = effective_order(config)
    return [
        SeparatorSpec(
            group=group,
            title=separator_title(group),
            summary=separator_summary(group),
            sort_title=separator_sort_title(group, order),
            poster_key=SEPARATOR_POSTER_KEYS.get(group),
        )
        for group in separator_groups(definitions, library_type, config)
    ]


def separator_titles(definitions, library_type: str, config) -> set[str]:
    """Every separator title these definitions imply.

    ``engine.definition_titles`` folds this in the way it already folds
    ``cs_bucket.titles()``: a title nothing enumerates is a title the delete
    sweep reads as an orphan, and the leftovers report reads as a prior tool's.
    """
    return {separator_title(group) for group in separator_groups(definitions, library_type, config)}
```

Fill `SEPARATOR_POSTER_KEYS` from Task 1's report before moving on. Every group whose measured stem returned `200` gets a row; every other group gets none.

- [ ] **Step 4: Add the config field and its validator**

In `src/autoposter/config/schema.py`, inside `CollectionsConfig`, immediately after the `separators` field (`:661-664`), replace that field's comment and add the new one:

```python
    # Blank "index card" divider collections -- one per group of collections
    # this service manages, each a permanently-empty collection whose sort
    # title floats it above its block in Plex's alphabetised collections tab
    # (roadmap row 49). Before row 49 this switch owned exactly one divider,
    # the Common Sense family's "Ratings Collections"; it now governs them all,
    # and that one is the content-ratings group's.
    separators: bool = True
    # Reorder the collection groups in the tab. None is the canonical order
    # (collections/groups.py: charts, awards, content ratings, content,
    # location, media, people, production, time, and the operator's own
    # definitions last). A PARTIAL list is the expected use: the groups it
    # names lead, in that order, and the rest follow canonically. Section
    # numbers derive from position, so changing this re-writes the sort title
    # of every collection this service manages, once, on the next pass.
    group_order: list[str] | None = None
```

Add this validator alongside the existing ones (immediately before `_titles_must_not_collide`):

```python
    @model_validator(mode="after")
    def _group_order_must_name_known_groups(self) -> "CollectionsConfig":
        """Every name in ``group_order`` is a group, and names it once.

        Refused here rather than discovered as a group whose collections
        quietly kept the canonical number: a mis-typed group is a reordering an
        operator believes they asked for, which is exactly the shape
        ``_presets_must_be_known_and_ready`` above refuses one field along.

        Imported at validation time, not module scope -- ``groups`` reaches back
        into this module through ``catalog``, the cycle every validator in this
        class documents.
        """
        from autoposter.collections.groups import CANONICAL_ORDER

        seen: set[str] = set()
        for name in self.group_order or []:
            if name in seen:
                raise ValueError(
                    f"collection group {name!r} is listed twice in "
                    "'group_order': a group has one position, so a repeated "
                    "name means less than it looks like"
                )
            seen.add(name)
            if name not in CANONICAL_ORDER:
                raise ValueError(
                    f"unknown collection group {name!r}: the groups are "
                    + ", ".join(CANONICAL_ORDER)
                )
        return self
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker compose -p p49t2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_collection_groups.py 2>&1 \
    | tee .superpowers/p49-t2-green.log
```

Expected: all tests pass. If `test_a_preset_definition_takes_its_presets_category` fails on the preset key, read `collections/catalog.py`'s `BY_KEY` for a READY preset in the `charts` category and use that key instead — the assertion is about the join, not about that particular preset.

- [ ] **Step 6: Prove nothing else moved**

```bash
docker compose -p p49t2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_config_schema.py tests/test_collection_catalog.py \
    tests/test_builder_port_golden.py 2>&1 | tee .superpowers/p49-t2-regress.log
docker compose -p p49t2 down
```

Expected: all pass. The golden gate in particular must be untouched — Task 2 adds a module and a config field and changes no reconcile path.

- [ ] **Step 7: Commit**

```bash
git add src/autoposter/collections/groups.py src/autoposter/config/schema.py \
        tests/test_collection_groups.py
git commit --no-gpg-sign -m "feat(collections): derive each collection's group and sort-title prefix"
```

Verify: `git show --stat HEAD` lists exactly those three files.

---

### Task 3: Separators generalised — one per group, by design

**Files:**
- Modify: `src/autoposter/collections/reconcile.py:36-53` (constants), `:490-567` (`_reconcile_separator`), `:570-802` (`reconcile_content_ratings`)
- Modify: `src/autoposter/collections/builders/cs_bucket.py:79-114`
- Modify: `src/autoposter/collections/engine.py` (`run_library`, `definition_titles`)
- Create: `tests/test_collection_group_separators.py`
- Modify: `tests/test_collection_separator.py` (call sites only)
- Modify: `tests/fixtures/collections/golden_port.json`, `tests/test_builder_port_golden.py` (amendment 1)

**Interfaces:**
- Consumes from Task 2: `groups.SeparatorSpec`, `groups.separator_specs`, `groups.separator_titles`, `groups.separator_title`.
- Produces, and Task 4 relies on these:
  - `reconcile.separator_hash(title: str, summary: str, sort_title: str) -> str`
  - `reconcile.reconcile_separator(session, section, library_name, libtype, label, spec, existing, stored, adopt, adopt_from, adopt_removes_prior_label, dry_run, protect_labels, http=None, config=None) -> list[str]`
  - `engine._separators(session, section, library, library_type, definitions, config, label, dry_run, listing) -> list[DefinitionResult]`
  - `reconcile.SEPARATOR_TITLE` and `reconcile.SEPARATOR_SUMMARY` still exist and now read `groups.separator_title("content_ratings")` / `groups.separator_summary("content_ratings")`. `SEPARATOR_SORT_TITLE` and `SEPARATOR_HASH` are **gone**.
  - `reconcile_content_ratings` no longer takes `separators`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_collection_group_separators.py`. The fakes are deliberately a fresh, minimal pair rather than an import from `tests/test_collection_separator.py` — row 191 filed the eight forked `section` doubles in this suite as debt and this phase does not consolidate them mid-flight (adjudication C6).

```python
"""Row 49: one blank divider per active collection group.

The lifecycle is BY DESIGN, and it is the part worth reading twice. A group
that stops having collections -- its last definition removed, its preset
switched off, or ``collections.separators`` turned off -- makes its separator an
ORDINARY delete-sweep candidate: nothing here deletes it, and it goes through
every guard the sweep applies to any other orphan (the ownership label AND a
managed row AND no protected label AND ``delete_unconfigured`` AND
``max_deletes``). With the sweep off, which is the default, it is reported and
left standing.
"""
from sqlalchemy import select

from autoposter.collections import groups
from autoposter.collections.reconcile import reconcile_separator, separator_hash
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"

SHIPPED_TITLE = "Ratings Collections"
SHIPPED_SUMMARY = "Section separator for Ratings Collections."
SHIPPED_SORT_TITLE = "!110_!Ratings Collections"
# The constant this module shipped, as a literal. The function has to reproduce
# it byte for byte or every live server takes a spurious re-write on the first
# pass after row 49 -- the hash is what a pass short-circuits on.
SHIPPED_HASH = "e22a14288c7962ea13a31e307aedf7e1ca204f6bf93238d65d0e436dcd5a4fb5"


def test_separator_hash_reproduces_the_shipped_constant_byte_for_byte():
    assert separator_hash(SHIPPED_TITLE, SHIPPED_SUMMARY, SHIPPED_SORT_TITLE) == SHIPPED_HASH


def test_a_new_section_number_changes_the_hash_exactly_once():
    now = separator_hash(SHIPPED_TITLE, SHIPPED_SUMMARY, "!030_!Ratings Collections")
    assert now != SHIPPED_HASH
    assert now == separator_hash(SHIPPED_TITLE, SHIPPED_SUMMARY, "!030_!Ratings Collections")


class FakeLabel:
    def __init__(self, tag):
        self.tag = tag


class FakeField:
    def __init__(self, name, locked):
        self.name, self.locked = name, locked


class FakeServer:
    def __init__(self):
        self.queries = []
        self._session = type("S", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://FAKE/com.plexapp.plugins.library"

    def query(self, path, method=None):
        self.queries.append((path, method))


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="1", summary="", sort_title=""):
        self.title = title
        self.ratingKey = rating_key
        self.summary = summary
        self.titleSort = sort_title
        self._real_labels = [FakeLabel(t) for t in labels]
        self.labels = []
        self.fields = [FakeField("summary", False)]
        self._server = FakeServer()
        self.labels_added = []
        self.sort_title_set = None

    def reload(self):
        self.labels = list(self._real_labels)

    def addLabel(self, tag):
        self.labels_added.append(tag)
        self._real_labels.append(FakeLabel(tag))
        self.labels = list(self._real_labels)

    def removeLabel(self, tag):
        self._real_labels = [l for l in self._real_labels if l.tag != tag]
        self.labels = list(self._real_labels)

    def editSortTitle(self, value):
        self.sort_title_set = value
        self.titleSort = value


class FakeSection:
    def __init__(self, collections=()):
        self.key = 42
        self._server = FakeServer()
        self._collections = {c.title: c for c in collections}
        self.created = []

    def collections(self):
        return list(self._collections.values())

    def collection(self, title):
        made = FakeCollection(title)
        self._collections[title] = made
        self.created.append(title)
        return made


def spec(group="charts", order=groups.CANONICAL_ORDER):
    return groups.SeparatorSpec(
        group=group,
        title=groups.separator_title(group),
        summary=groups.separator_summary(group),
        sort_title=groups.separator_sort_title(group, order),
        poster_key=groups.SEPARATOR_POSTER_KEYS.get(group),
    )


async def run(session, section, target, existing=None, stored=None, dry_run=False,
              adopt=False, adopt_from=(), protect_labels=()):
    # ``session`` is tests/conftest.py's own AsyncSession fixture (pytest's
    # asyncio_mode is "auto" in pyproject.toml:69, so no marker is needed).
    return await reconcile_separator(
        session, section, "Movies", "movie", LABEL, target,
        existing if existing is not None else {},
        stored if stored is not None else {},
        adopt, list(adopt_from), False, dry_run, list(protect_labels),
    )


async def test_a_missing_separator_is_created_with_its_groups_sort_title(session):
    section = FakeSection()
    target = spec("charts")
    actions = await run(session, section, target)

    assert actions == ["created 'Chart Collections'"]
    assert section.created == ["Chart Collections"]
    made = section._collections["Chart Collections"]
    assert made.sort_title_set == "!010_!Chart Collections"
    assert made.labels_added == [LABEL]

    row = (await session.execute(select(ManagedCollection))).scalars().one()
    assert (row.title, row.kind) == ("Chart Collections", "separator")
    assert row.definition_hash == separator_hash(
        target.title, target.summary, target.sort_title
    )


async def test_a_current_separator_writes_nothing(session):
    target = spec("charts")
    made = FakeCollection("Chart Collections", labels=[LABEL],
                          sort_title=target.sort_title, summary=target.summary)
    row = ManagedCollection(
        library="Movies", title="Chart Collections", kind="separator",
        plex_rating_key="1",
        definition_hash=separator_hash(target.title, target.summary, target.sort_title),
    )
    session.add(row)
    await session.flush()

    actions = await run(session, FakeSection([made]), target,
                        existing={"Chart Collections": made},
                        stored={"Chart Collections": row})
    assert actions == []
    assert made.sort_title_set is None


async def test_each_group_gets_its_own_separator(session):
    for group, title, sort in [
        ("charts", "Chart Collections", "!010_!Chart Collections"),
        ("awards", "Award Collections", "!020_!Award Collections"),
        ("content_ratings", "Ratings Collections", "!030_!Ratings Collections"),
    ]:
        section = FakeSection()
        actions = await run(session, section, spec(group))
        assert actions == ["created %r" % title]
        assert section._collections[title].sort_title_set == sort


async def test_a_dry_run_writes_nothing(session):
    section = FakeSection()
    actions = await run(session, section, spec("charts"), dry_run=True)
    assert actions == ["would create 'Chart Collections'"]
    assert section.created == []
    assert (await session.execute(select(ManagedCollection))).scalars().all() == []


async def test_an_unlabelled_collision_is_refused_not_claimed(session):
    theirs = FakeCollection("Chart Collections")
    actions = await run(session, FakeSection([theirs]), spec("charts"),
                        existing={"Chart Collections": theirs})
    assert actions == [
        "conflict: 'Chart Collections' exists without the 'autoposter' label; "
        "leaving it untouched"
    ]
    assert theirs.sort_title_set is None


async def test_an_operator_blank_is_never_written_over(session):
    # The ops/blank hazard: an operator made a blank collection under a title a
    # group later claims. It carries OUR ownership label (the endpoint adds it),
    # so resolve_collision approves it -- and its managed row is what says whose
    # it is. Refused and reported; nothing is written and the row keeps saying
    # "operator".
    theirs = FakeCollection("Chart Collections", labels=[LABEL])
    row = ManagedCollection(
        library="Movies", title="Chart Collections", kind="operator",
        plex_rating_key="1", definition_hash="",
    )
    session.add(row)
    await session.flush()

    actions = await run(session, FakeSection([theirs]), spec("charts"),
                        existing={"Chart Collections": theirs},
                        stored={"Chart Collections": row})
    assert actions == [
        "'Chart Collections' was created by an operator, not by any definition; "
        "the 'charts' group's separator is not written over it"
    ]
    assert theirs.sort_title_set is None
    assert row.kind == "operator"
    assert row.definition_hash == ""


async def test_a_protected_label_wins(session):
    theirs = FakeCollection("Chart Collections",
                            labels=[LABEL, "Collection managed by Maintainerr"])
    actions = await run(session, FakeSection([theirs]), spec("charts"),
                        existing={"Chart Collections": theirs},
                        protect_labels=["Collection managed by Maintainerr"])
    assert actions == [
        "protected: 'Chart Collections' carries 'Collection managed by "
        "Maintainerr'; leaving it untouched"
    ]
    assert theirs.sort_title_set is None


def test_a_group_that_goes_quiet_leaves_an_ordinary_sweep_candidate():
    # By design, and the whole of it: nothing in the separator path deletes,
    # so a group with no collections simply stops appearing in
    # ``separator_titles`` -- which is what makes its divider an orphan like any
    # other, judged only by ``engine._sweep``'s guards.
    from types import SimpleNamespace

    from autoposter.config.schema import CollectionDefinition, CollectionsConfig

    config = SimpleNamespace(collections=CollectionsConfig())
    charts = [CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                                   params={"chart": "top"})]
    assert groups.separator_titles(charts, "Movie", config) == {"Chart Collections"}
    assert groups.separator_titles([], "Movie", config) == set()

    off = SimpleNamespace(collections=CollectionsConfig(separators=False))
    assert groups.separator_titles(charts, "Movie", off) == set()
```

Add to `tests/test_collection_groups.py` (the engine's enumeration seam):

```python
def test_definition_titles_counts_the_separator_titles():
    from types import SimpleNamespace

    from autoposter.collections.engine import definition_titles
    from autoposter.config.schema import CollectionDefinition, CollectionsConfig

    config = SimpleNamespace(collections=CollectionsConfig())
    definitions = [
        CollectionDefinition(title="IMDb Top 250", builder="imdb_chart",
                             params={"chart": "top"}),
    ]
    titles = definition_titles(definitions, [], "Movie", config)
    assert "IMDb Top 250" in titles
    assert "Chart Collections" in titles

    off = SimpleNamespace(collections=CollectionsConfig(separators=False))
    assert "Chart Collections" not in definition_titles(definitions, [], "Movie", off)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose -p p49t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_collection_group_separators.py \
    tests/test_collection_groups.py 2>&1 | tee .superpowers/p49-t3-red.log
```

Expected: `ImportError: cannot import name 'reconcile_separator'` / `'separator_hash'` from `autoposter.collections.reconcile`.

- [ ] **Step 3: Replace the separator constants with the function**

In `src/autoposter/collections/reconcile.py`, replace lines 36-53 with:

```python
# The Common Sense family's divider, kept as named constants because two
# neighbours read them: ``cs_bucket`` and the catalog's setting row. Both values
# are now DERIVED from the group machinery rather than written out here --
# "Ratings Collections" is what the content-ratings group's separator is called,
# and one spelling of it is the point (roadmap row 49).
SEPARATOR_TITLE = groups.separator_title("content_ratings")
SEPARATOR_SUMMARY = groups.separator_summary("content_ratings")


def separator_hash(title: str, summary: str, sort_title: str) -> str:
    """Hash one separator's whole desired state.

    Was a module CONSTANT: there was one separator, its desired state never
    varied by library, and ``definition_hash``'s ``Bucket`` shape does not fit
    it. Row 49 made separators plural, so the constant became this -- and it
    computes the SAME payload in the SAME order, which is what keeps the
    shipped digest reproducible byte for byte
    (``tests/test_collection_group_separators.py`` pins it). Anything else
    would give every live server a spurious re-write of a collection whose
    desired state had not changed, because the hash is what a pass
    short-circuits on.
    """
    return hashlib.sha256(
        "\x1f".join([title, summary, sort_title]).encode("utf-8")
    ).hexdigest()
```

Add `from autoposter.collections import groups` to the import block at the top (it is import-cheap by construction — see that module's docstring).

The template citation that lived in the deleted comment is not lost: it now lives in `groups.py`'s module docstring, which names both formulas and their line numbers. Leave a one-line pointer here:

```python
# The two upstream formulas these values follow -- the separator's name and its
# sort title -- are transcribed and cited in ``collections/groups.py``'s module
# docstring. The section NUMBERS are ours (NOT_KOMETA), and so is the tenth
# group.
```

- [ ] **Step 4: Turn `_reconcile_separator` into the spec-driven public routine**

Replace `reconcile.py:490-567` with:

```python
async def reconcile_separator(
    session: AsyncSession,
    section,
    library_name: str,
    libtype: str,
    label: str,
    spec: "groups.SeparatorSpec",
    existing: dict,
    stored: dict,
    adopt: bool,
    adopt_from: list[str],
    adopt_removes_prior_label: bool,
    dry_run: bool,
    protect_labels: list[str],
    http: httpx.AsyncClient | None = None,
    config=None,
) -> list[str]:
    """One group's blank divider: created, kept current, never populated.

    Same ownership and adoption rules as every other collection this service
    manages -- the shared ``resolve_collision`` -- and nothing here ever calls
    ``addItems``. It owns its own sort title (the ``spec``'s), which is why the
    shared ``apply_collection_settings`` is deliberately not called on it: that
    would hand it the group's MEMBER prefix and sink the heading into its own
    block.

    Was ``_reconcile_separator``, a private routine over three module constants,
    reached only from ``reconcile_content_ratings``. Row 49 made separators
    plural, so it takes a ``SeparatorSpec`` and the engine drives it once per
    active group -- which is the whole of "generalise the one into N".

    **Deletion is not here, by design.** A group that stops having collections
    stops being named by ``groups.separator_titles``, and its divider becomes an
    ordinary candidate for ``engine._sweep`` -- through the ownership label, the
    managed row, any protecting label, ``delete_unconfigured`` (off by default,
    and off means reported) and ``max_deletes``. Nothing about a heading earns
    it a shortcut past guards every other collection has.
    """
    collection = existing.get(spec.title)
    record = stored.get(spec.title)
    actions: list[str] = []

    if record is not None and record.kind == "operator":
        # The ops/blank hazard. An operator blanked a collection under a title
        # a group now claims; the endpoint gave it OUR ownership label, so
        # resolve_collision would approve it and this routine would write a
        # summary and a sort title over something nobody asked it to touch.
        # The managed row is what says whose it is, and "operator" is the kind
        # ``api/collections_builders.py`` writes precisely so this decision is
        # possible. Returned, not raised: one contested title must not cost the
        # library its pass.
        return [
            "%r was created by an operator, not by any definition; the %r "
            "group's separator is not written over it" % (spec.title, spec.group)
        ]

    if collection is not None:
        ok, message = resolve_collision(
            collection, label, adopt, adopt_from, adopt_removes_prior_label, dry_run,
            protect_labels,
        )
        if message:
            actions.append(message)
        if not ok:
            return actions

    posters_on = posters_enabled(config, http)
    wanted = separator_hash(spec.title, spec.summary, spec.sort_title)
    definition_current = (
        collection is not None and record is not None
        and record.definition_hash == wanted
    )
    if definition_current and not (posters_on and record.poster_sha256 is None):
        return actions

    if not definition_current:
        if dry_run:
            actions.append(
                "%s %r" % ("would update" if collection else "would create", spec.title)
            )
        else:
            if collection is None:
                collection = create_blank_collection(section, libtype, spec.title)
                collection.addLabel(label)
                actions.append("created %r" % spec.title)
            else:
                actions.append("updated %r" % spec.title)

            _edit_collection_summary(collection, spec.summary)
            collection.editSortTitle(spec.sort_title)

            if record is None:
                record = ManagedCollection(
                    library=library_name, title=spec.title, kind="separator",
                    plex_rating_key=str(getattr(collection, "ratingKey", "") or ""),
                    definition_hash=wanted,
                )
                session.add(record)
            else:
                record.definition_hash = wanted
                record.plex_rating_key = str(getattr(collection, "ratingKey", "") or "")

    if (
        posters_on and collection is not None and record is not None
        and spec.poster_key is not None
    ):
        # A group with no measured artwork stem gets no poster rather than a
        # guessed path: a wrong URL 404s and the collection quietly keeps none,
        # which is harder to spot than an absence
        # (``posters.hosted_poster_url``'s own rule, applied one level up).
        message = await apply_poster(
            session, http, config, collection, record, library_name,
            "separator", spec.poster_key, dry_run=dry_run,
        )
        if message:
            actions.append(message)

    return actions
```

- [ ] **Step 5: Take the separator out of the Common Sense reconciler**

In `reconcile.py`:

1. Delete the `separators: bool = False` parameter (`:580`) from `reconcile_content_ratings`.
2. Delete its docstring paragraph (`:603-607`) and replace it with one line: *"The family's divider is no longer reconciled here — every group's separator is driven by the engine (`engine._separators`), which is what made one divider into N (roadmap row 49)."*
3. Delete the `if separators:` block (`:794-799`).

In `src/autoposter/collections/builders/cs_bucket.py`:

4. Delete `separators=ctx.config.collections.separators,` from the `apply` call (`:90`).
5. In `titles()` (`:103-114`), delete the `if config.collections.separators: titles.add(SEPARATOR_TITLE)` branch and its now-unused import, and replace the docstring's last paragraph with:

```python
        """Every title this builder manages for one library.

        ``derive_buckets`` only varies a bucket's *filter values* by the ratings
        actually present -- its titles come from the key/library-type pair alone
        -- so an empty ``present`` set recovers every bucket title without a
        Plex round trip.

        The family's divider is NOT here since row 49. It is the content-ratings
        group's separator now, and ``engine.definition_titles`` enumerates every
        group's through ``groups.separator_titles`` -- one owner for the title,
        so a divider cannot be counted by one path and swept by another.
        """
```

- [ ] **Step 6: Drive the separators from the engine**

In `src/autoposter/collections/engine.py`, add `from autoposter.collections import groups` to the imports and add the separator term to `definition_titles` (`:964-987`), immediately before `return titles`:

```python
    # Every active group's blank divider. Folded in here rather than by the
    # callers, so the delete sweep, the leftovers report and the config-load
    # collision check all see the same set -- a title one of them missed is a
    # heading the sweep reads as an orphan or the report reads as a prior
    # tool's. ``config`` is read only through ``config.collections``, which is
    # what lets ``CollectionsConfig._titles_must_not_collide`` call this with a
    # SimpleNamespace shim of that one section.
    titles |= groups.separator_titles(definitions, library_type, config)
    return titles
```

Add the driver, immediately before `def definition_titles_for`:

```python
async def _separators(
    session: AsyncSession,
    section,
    library: str,
    library_type: str,
    definitions: list[CollectionDefinition],
    config,
    label: str,
    dry_run: bool,
    listing,
) -> list[DefinitionResult]:
    """One blank divider per group these definitions put collections in.

    After the definitions rather than before, for one reason: the set of active
    groups is derived FROM them, so running first would mean deciding what the
    pass built before it had built it. Before the sweep, because a divider
    created this pass has to be in the managed set the sweep reads.

    One result per separator, under its own title. The Common Sense divider used
    to fold its actions into the family's single result; a heading is now its own
    thing in every library, and a preview that hid three of them inside one
    family's row would be reporting the shape this phase replaced.
    """
    specs = groups.separator_specs(
        [d for d in definitions if _targets(d, library)], library_type, config,
    )
    if not specs:
        return []

    stored = {
        row.title: row
        for row in (
            await session.execute(
                select(ManagedCollection).where(ManagedCollection.library == library)
            )
        ).scalars()
    }
    collections = config.collections
    results: list[DefinitionResult] = []
    for spec in specs:
        actions = await reconcile_separator(
            session, section, library, LIBTYPES[library_type], label, spec,
            listing(), stored,
            collections.adopt, collections.adopt_from or [],
            collections.adopt_removes_prior_label, dry_run,
            collections.protect_labels or [],
        )
        results.append(DefinitionResult(
            title=spec.title, library=library, actions=list(actions), skipped=True,
        ))
    return results
```

Add the imports this needs at the top of `engine.py`: `reconcile_separator` and `LIBTYPES` from `autoposter.collections.reconcile`, `ManagedCollection` from `autoposter.db.models`, and `select` from `sqlalchemy` — check which are already imported before adding (the sweep already uses `select` and `ManagedCollection`).

Call it in `run_library`, between the definitions loop and the `if sweep:` block (`engine.py:400-401`):

```python
    for result in await _separators(
        session, section, library, library_type, definitions, config,
        label=label, dry_run=dry_run, listing=listing,
    ):
        actions += result.actions
        results.append(result)

    if sweep:
```

Deliberately **not** wrapped in a `try`: a separator write failing is a Plex write failing, which belongs to `reconcile_libraries`' per-library rollback exactly like every other write in the pass. The sweep's wrapper exists because it runs READS below writes already committed; this does not.

- [ ] **Step 7: Update `tests/test_collection_separator.py`'s call sites — minimally**

That file's `reconcile_content_ratings(..., separators=True, ...)` calls no longer have that parameter, and `SEPARATOR_SORT_TITLE`/`SEPARATOR_HASH` no longer exist. For each test in it:

- Replace the import line with `from autoposter.collections.reconcile import SEPARATOR_SUMMARY, SEPARATOR_TITLE, reconcile_separator, separator_hash`.
- Replace each `reconcile_content_ratings(..., separators=True, ...)` call with the equivalent `reconcile_separator(...)` call, building the spec with `groups.separator_specs([CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket")], "Movie", config)[0]`.
- Replace `SEPARATOR_SORT_TITLE` with `"!030_!Ratings Collections"` and `SEPARATOR_HASH` with `separator_hash(SEPARATOR_TITLE, SEPARATOR_SUMMARY, "!030_!Ratings Collections")`.

**Do not** consolidate its forked `FakeSection`/`FakeCollection` doubles with any other file's — roadmap row 191 owns that debt and adjudication C6 keeps it out of this phase. Add one line to the module docstring saying so:

```python
Row 49 moved the reconcile itself out of ``reconcile_content_ratings`` and onto
``reconcile_separator``, which takes a ``groups.SeparatorSpec``; these tests
follow it there. The forked ``section`` double below is roadmap row 191's debt
and is deliberately left forked -- consolidating it mid-phase would put a
refactor of eight files inside a behaviour change.
```

- [ ] **Step 8: Run the separator suites**

```bash
docker compose -p p49t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_collection_group_separators.py \
    tests/test_collection_separator.py tests/test_collection_groups.py \
    tests/test_collection_reconcile.py tests/test_collection_engine.py 2>&1 \
    | tee .superpowers/p49-t3-green.log
```

Expected: all pass.

- [ ] **Step 9: Commit the code, before the fixture**

```bash
git add src/autoposter/collections/reconcile.py \
        src/autoposter/collections/engine.py \
        src/autoposter/collections/builders/cs_bucket.py \
        tests/test_collection_group_separators.py \
        tests/test_collection_separator.py tests/test_collection_groups.py
git commit --no-gpg-sign -m "feat(collections): one blank separator per collection group"
```

Verify: `git show --stat HEAD` does **not** list `tests/fixtures/collections/golden_port.json`. The fixture amendment is the next, separate commit — that separation is the whole point of the gate.

- [ ] **Step 10: Back up the golden fixture and re-capture it**

```bash
cp tests/fixtures/collections/golden_port.json .superpowers/p49-golden-before-t3.json

docker compose -p p49t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -e AUTOPOSTER_GOLDEN_CAPTURE=1 test \
    pytest -q tests/test_builder_port_golden.py 2>&1 \
    | tee .superpowers/p49-t3-capture.log
```

Expected: the run FAILS. That is by design — a capture run writes the fixture and then fails so it can never be mistaken for a passing gate (`tests/test_builder_port_golden.py`'s module docstring).

- [ ] **Step 11: Gate the diff — separator rows only**

```bash
git diff --stat tests/fixtures/collections/golden_port.json
git diff -U0 tests/fixtures/collections/golden_port.json \
    | grep -E '^[-+][^-+]' > .superpowers/p49-t3-golden-diff.txt
cat .superpowers/p49-t3-golden-diff.txt
```

Read every line. **Every one must be one of:**

1. an action string whose quoted subject is a separator title (`'Chart Collections'`, `'Award Collections'`, `'Ratings Collections'`);
2. a line inside a `state` object keyed by one of those three titles;
3. the `"sort_title": "!110_!Ratings Collections"` → `"!030_!Ratings Collections"` cell.

Any line naming a non-separator collection is a defect in Task 3, not something to accept into the fixture. Restore the backup, fix the code, and re-capture:

```bash
cp .superpowers/p49-golden-before-t3.json tests/fixtures/collections/golden_port.json
```

Then prove the gate passes with the fixture as captured:

```bash
docker compose -p p49t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_builder_port_golden.py 2>&1 \
    | tee .superpowers/p49-t3-golden-green.log
docker compose -p p49t3 down
```

Expected: PASS.

- [ ] **Step 12: Document the amendment in the gate's own docstring, then commit it**

Add to `tests/test_builder_port_golden.py`'s module docstring, after the phase-10a-2 paragraph:

```
**Second deliberate amendment, row 49 (separators).** The one hand-built
"Ratings Collections" divider became one divider per collection GROUP
(`collections/groups.py`), driven by the engine after the definitions rather
than from inside the Common Sense reconciler. So the charts and awards families
gained headings of their own, the Ratings heading moved to the end of the
pass's actions with them, and its own sort title took the content-ratings
group's section number (`!110_!` -> `!030_!`) -- ours, not Kometa's, and stated
as such in `.superpowers/sdd/p49-facts.md` C2. Adjudicated in advance (C4), the
ONLY cells that moved are separator ones, it landed as its own reviewed commit,
and `tests/test_collection_group_separators.py` grades the behaviour this
fixture can only witness.
```

```bash
git add tests/fixtures/collections/golden_port.json tests/test_builder_port_golden.py
git commit --no-gpg-sign -m "test(collections): amend the golden gate for the per-group separators

The only cells that move are separator action strings, separator state entries
and the Ratings divider's own sort title (!110_! -> !030_!). Adjudicated in
.superpowers/sdd/p49-facts.md C4. Re-capture:

  docker compose -p p49t3 -f docker-compose.yml -f .superpowers/isolated-db.yml \\
      run --rm -e AUTOPOSTER_GOLDEN_CAPTURE=1 test \\
      pytest -q tests/test_builder_port_golden.py"
rm .superpowers/p49-golden-before-t3.json
```

---

### Task 4: The derived sort title, end to end

**Files:**
- Modify: `src/autoposter/collections/lists.py:169-200` (signature + wrap)
- Modify: `src/autoposter/collections/smart.py:218-260` (signature + wrap)
- Modify: `src/autoposter/collections/reconcile.py` (`reconcile_content_ratings` signature + the bucket loop at `:738`, `:770`)
- Modify: `src/autoposter/collections/builders/base.py:196-211` (`SmartContext`)
- Modify: `src/autoposter/collections/builders/cs_bucket.py:79-101`, `builders/smart_filter.py:190-205`, `builders/dynamic.py:855-880`
- Modify: `src/autoposter/collections/engine.py` (`run_library`, `_run_one`)
- Modify: `tests/test_collection_groups.py` (integration assertions)
- Modify: `tests/fixtures/collections/golden_port.json` (amendment 2)

**Interfaces:**
- Consumes from Task 2: `groups.effective_order`, `groups.preset_groups`, `groups.sort_prefix_for`, `groups.with_derived_sort_title`.
- Consumes from Task 3: nothing — separators own their own sort title and are never routed through `apply_collection_settings`.
- Produces: `sort_prefix: str | None = None` as the last keyword parameter of `reconcile_list_collection`, `reconcile_smart_collection` and `reconcile_content_ratings`; `SmartContext.sort_prefix: str | None = None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_collection_groups.py`:

```python
async def test_a_chart_collection_gets_its_groups_prefix(session, chart_section):
    """The end-to-end shape: no sort_title in the config, one in Plex."""
    from autoposter.collections.lists import reconcile_list_collection
    from autoposter.config.schema import CollectionDefinition

    definition = CollectionDefinition(
        title="IMDb Top 250", builder="imdb_chart", params={"chart": "top"}
    )
    await reconcile_list_collection(
        session, chart_section, "Movies", "IMDb Top 250",
        chart_section.items, "autoposter", dry_run=False,
        settings=definition, sort_prefix="!010_",
    )
    made = chart_section._collections["IMDb Top 250"]
    assert made.sort_title_set == "!010_IMDb Top 250"


async def test_an_explicit_sort_title_still_wins_end_to_end(session, chart_section):
    from autoposter.collections.lists import reconcile_list_collection
    from autoposter.config.schema import CollectionDefinition

    definition = CollectionDefinition(
        title="IMDb Top 250", builder="imdb_chart", params={"chart": "top"},
        sort_title="!999_mine",
    )
    await reconcile_list_collection(
        session, chart_section, "Movies", "IMDb Top 250",
        chart_section.items, "autoposter", dry_run=False,
        settings=definition, sort_prefix="!010_",
    )
    assert chart_section._collections["IMDb Top 250"].sort_title_set == "!999_mine"


def test_each_member_of_a_family_gets_its_own_title_in_the_prefix():
    """A family shares a GROUP, not a sort title.

    ``CollectionDefinition.sort_title``'s docstring says an explicit one goes to
    every collection of a family verbatim, and that is unchanged. A DERIVED one
    is per collection: the block is what the section number makes, and inside it
    each collection sorts by its own name.
    """
    from autoposter.config.schema import CollectionDefinition

    family = CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket")
    first = groups.with_derived_sort_title(family, "!030_", "Age 13+ Movies")
    second = groups.with_derived_sort_title(family, "!030_", "Age 17+ Movies")
    assert first.sort_title == "!030_Age 13+ Movies"
    assert second.sort_title == "!030_Age 17+ Movies"


def test_the_derived_value_changes_the_definition_hash_exactly_once():
    """The migration, in one assertion.

    A pass short-circuits on this hash. If the derived sort title did not reach
    it, the first pass after row 49 would find every hash current, skip every
    collection, and never write a sort title at all -- the trap C3 names.
    """
    from autoposter.collections.buckets import Bucket
    from autoposter.collections.reconcile import definition_hash
    from autoposter.config.schema import CollectionDefinition

    bucket = Bucket(key="13", title="Age 13+ Movies",
                    summary="...", values=("PG-13",))
    family = CollectionDefinition(title="Common Sense age ratings", builder="cs_bucket")
    before = definition_hash(bucket, family, "url")
    view = groups.with_derived_sort_title(family, "!030_", bucket.title)
    after = definition_hash(bucket, view, "url")
    assert before != after
    assert after == definition_hash(
        bucket, groups.with_derived_sort_title(family, "!030_", bucket.title), "url"
    )


def test_expansion_never_inherits_a_derived_sort_title():
    """The other C3 trap. ``engine._completed`` fills an expanded unit from the
    placeholder for every field the unit did not set -- reading
    ``model_fields_set``. A derived value written onto the placeholder would be
    marked set, and all five Oscars year collections would share one sort title
    instead of each getting its own."""
    from autoposter.collections.engine import _completed
    from autoposter.config.schema import CollectionDefinition

    placeholder = CollectionDefinition(
        title="Oscars Winners (recent ceremonies)", builder="imdb_award_years"
    )
    groups.with_derived_sort_title(placeholder, "!020_", placeholder.title)
    unit = CollectionDefinition(title="Oscars Winners 2026", builder="imdb_award_years")
    assert _completed(placeholder, unit).sort_title is None
```

`chart_section` is a fixture the file needs; add it near the top of `tests/test_collection_groups.py`, reusing the fake shapes from `tests/test_collection_group_separators.py` by import (they are in this phase's own new file, so importing them is not row 191's forked-double debt):

```python
@pytest.fixture
def chart_section():
    from tests.test_collection_group_separators import FakeSection

    section = FakeSection()
    section.items = [SimpleNamespace(ratingKey=1), SimpleNamespace(ratingKey=2)]
    return section
```

If `reconcile_list_collection` needs more of the section double than `FakeSection` provides (it adds and orders members, which the separator double never does), extend `FakeSection` in `tests/test_collection_group_separators.py` with the `addItems`/`moveItem`/`sortUpdate` no-ops it asks for rather than forking a third double — a new double in this phase's own files would be row 191's defect being committed fresh.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker compose -p p49t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_collection_groups.py 2>&1 \
    | tee .superpowers/p49-t4-red.log
```

Expected: `TypeError: reconcile_list_collection() got an unexpected keyword argument 'sort_prefix'`.

- [ ] **Step 3: Thread `sort_prefix` through the three reconcilers**

In `src/autoposter/collections/lists.py`, add the parameter after `settings=None` in `reconcile_list_collection`'s signature:

```python
    sort_prefix: str | None = None,
```

and this paragraph to its docstring, after the `settings` one:

```
    ``sort_prefix`` is the collection GROUP's sort-title prefix (``"!010_"``),
    resolved engine-side (``collections/groups.py``, roadmap row 49). It is
    applied here rather than by the caller because only here is the concrete
    collection title known -- a family's members share a prefix and not a sort
    title. A definition that names its own ``sort_title`` keeps it; None derives
    nothing, which is what a direct caller with no pass around it gets.
```

Immediately after the `if not items:` early return, add:

```python
    # Out of band and read-only: the definition itself is never touched, so
    # nothing downstream can mistake a derived value for one the operator wrote
    # (``groups._DerivedSortTitle``). Placed here so the value is in front of
    # ``_settings_parts`` below -- the hash is what a pass short-circuits on, and
    # a sort title that appeared only at write time would never trigger one.
    settings = groups.with_derived_sort_title(settings, sort_prefix, title)
```

Add `from autoposter.collections import groups` to `lists.py`'s imports.

Do the same in `src/autoposter/collections/smart.py` for `reconcile_smart_collection`: same parameter, same docstring paragraph, and the wrap placed before `smart_definition_hash` is computed.

In `src/autoposter/collections/reconcile.py`, add the same parameter to `reconcile_content_ratings` and, inside the bucket loop, immediately before `wanted = definition_hash(bucket, settings, url)` (`:738`):

```python
        # Per BUCKET, not per definition: one definition names the whole
        # family, and the group's prefix is what they share -- inside the block
        # each bucket sorts by its own title. That is why the wrap is here and
        # not at the top of the function.
        bucket_settings = groups.with_derived_sort_title(
            settings, sort_prefix, bucket.title
        )
```

then use `bucket_settings` in both `definition_hash(bucket, bucket_settings, url)` (`:738`) and `apply_collection_settings(section, collection, bucket_settings, label, config)` (`:770-772`).

- [ ] **Step 4: Carry the prefix to the smart builders**

In `src/autoposter/collections/builders/base.py`, add to `SmartContext` after `definition` (`:209`):

```python
    # The collection group's sort-title prefix for this definition, resolved by
    # the engine (``collections/groups.py``, roadmap row 49). Handed to a smart
    # builder for exactly the reason ``definition`` is: a smart builder applies
    # its own collections, so everything the engine passes to
    # ``reconcile_list_collection`` has to reach the smart reconcilers the same
    # way. None for a direct caller that has no pass around it.
    sort_prefix: str | None = None
```

Forward it, one line each:

- `builders/cs_bucket.py:94` — add `sort_prefix=ctx.sort_prefix,` beside `settings=ctx.definition,`.
- `builders/smart_filter.py:200` — add `sort_prefix=ctx.sort_prefix,` beside `settings=definition,`.
- `builders/dynamic.py:878` — add `sort_prefix=ctx.sort_prefix,` beside `settings=settings,`. The dynamic engine passes one call per generated key and `reconcile_smart_collection` wraps by that key's own title, so each generated collection gets `!<NNN>_<its own title>` without dynamic having to know the scheme.

- [ ] **Step 5: Resolve the group in the engine**

In `src/autoposter/collections/engine.py`'s `run_library`, after `bound_sources` is built (`:320-323`), add:

```python
    # The group index and the tab order, resolved ONCE for the library rather
    # than per definition: ``preset_groups`` walks the catalog's active presets
    # and expanding it forty times would be forty identical scans. Pure -- no
    # Plex, no database -- so it costs nothing a dry run does not also pay.
    group_order = groups.effective_order(config)
    group_index = groups.preset_groups(config, library_type)
```

Pass it into the smart dispatch (`:360-367`), adding one argument to the `SmartContext(...)` construction:

```python
                    sort_prefix=groups.sort_prefix_for(
                        definition, group_index, group_order
                    ),
```

and into `_run_one` for the list path (`:394-399`), adding one argument to the call:

```python
                sort_prefix=groups.sort_prefix_for(unit, group_index, group_order),
```

**`unit`, not `definition`** — that is the whole of the C3 expansion trap. An expanded unit carries the ceremony's own builder (`imdb_award_years`) and its own title (`"Oscars Winners 2026"`), so it resolves its own group and its own sort title; resolving from the placeholder would give all five ceremony years one string.

In `_run_one`'s signature add `sort_prefix: str | None = None,` and pass it through to `reconcile_list_collection` beside `settings=definition` (`:554`):

```python
            settings=definition,
            sort_prefix=sort_prefix,
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
docker compose -p p49t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_collection_groups.py \
    tests/test_collection_group_separators.py tests/test_collection_lists.py \
    tests/test_collection_smart.py tests/test_collection_reconcile.py \
    tests/test_collection_dynamic.py tests/test_collection_engine.py 2>&1 \
    | tee .superpowers/p49-t4-green.log
```

Expected: all pass.

- [ ] **Step 7: Commit the code, before the fixture**

```bash
git add src/autoposter/collections/lists.py src/autoposter/collections/smart.py \
        src/autoposter/collections/reconcile.py src/autoposter/collections/engine.py \
        src/autoposter/collections/builders/base.py \
        src/autoposter/collections/builders/cs_bucket.py \
        src/autoposter/collections/builders/smart_filter.py \
        src/autoposter/collections/builders/dynamic.py \
        tests/test_collection_groups.py tests/test_collection_group_separators.py
git commit --no-gpg-sign -m "feat(collections): apply each group's sort-title prefix to its collections"
```

Verify: `git show --stat HEAD` does not list the golden fixture.

- [ ] **Step 8: Back up, re-capture, and gate the diff to `sort_title` lines only**

```bash
cp tests/fixtures/collections/golden_port.json .superpowers/p49-golden-before-t4.json

docker compose -p p49t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -e AUTOPOSTER_GOLDEN_CAPTURE=1 test \
    pytest -q tests/test_builder_port_golden.py 2>&1 \
    | tee .superpowers/p49-t4-capture.log

git diff -U0 tests/fixtures/collections/golden_port.json \
    | grep -E '^[-+][^-+]' > .superpowers/p49-t4-golden-diff.txt
grep -cvE '^[-+]\s*"sort_title":' .superpowers/p49-t4-golden-diff.txt
```

Expected: the last command prints `0`. **Every changed line is a `"sort_title":` line** — this is C5's gate, met exactly. Any other line means the sort-title change leaked into a summary, a member list or an action string; restore the backup, fix, re-capture.

```bash
cmp .superpowers/p49-golden-before-t4.json tests/fixtures/collections/golden_port.json \
    && echo "NOTHING CHANGED -- the derived value never reached the fixture, which is the C3 trap"
```

Expected: `cmp` reports the files differ. Identical files would mean the hash never changed and no sort title was ever written — the exact failure C3 exists to prevent.

Then prove the gate passes:

```bash
docker compose -p p49t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_builder_port_golden.py 2>&1 \
    | tee .superpowers/p49-t4-golden-green.log
docker compose -p p49t4 down
```

- [ ] **Step 9: Document and commit the amendment**

Add to `tests/test_builder_port_golden.py`'s module docstring, after Task 3's paragraph:

```
**Third deliberate amendment, row 49 (sort titles).** Every collection this
service manages now derives its group's `!<NNN>_` sort-title prefix, so the
`sort_title` cell moved from null to a derived string for every collection in
every scenario. Adjudicated in advance (`.superpowers/sdd/p49-facts.md` C5),
`"sort_title":` lines are the ONLY lines that moved, and it landed as its own
reviewed commit. What this fixture cannot reach --
that the value gets in out of band, that an explicit `sort_title` still wins,
and that expansion never inherits a derived one -- is graded by
`tests/test_collection_groups.py`.
```

```bash
git add tests/fixtures/collections/golden_port.json tests/test_builder_port_golden.py
git commit --no-gpg-sign -m "test(collections): amend the golden gate for the derived sort titles

Only \"sort_title\" lines move; the diff was gated on that and nothing else.
Adjudicated in .superpowers/sdd/p49-facts.md C5. Re-capture:

  docker compose -p p49t4 -f docker-compose.yml -f .superpowers/isolated-db.yml \\
      run --rm -e AUTOPOSTER_GOLDEN_CAPTURE=1 test \\
      pytest -q tests/test_builder_port_golden.py"
rm .superpowers/p49-golden-before-t4.json
```

---

### Task 5: Wrap — the full suite, the docs, the roadmap, the PR body

**Files:**
- Modify: `deploy/README.md:333-372`
- Modify: `config/autoposter.example.yaml:45-60`
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:101` (row 5) and `:145` (row 49)
- Create: `D:\Sites\autoposter\.superpowers\sdd\p49-pr-body.md`
- Create: `D:\Sites\autoposter\.superpowers\sdd\p49-report.md`

**Interfaces:**
- Consumes: everything from Tasks 1-4.
- Produces: the merge-ready branch and its PR body.

- [ ] **Step 1: Run the whole suite, detached, and wait in the foreground**

The suite is ~4 minutes; a `--rm` run streams through a log filter that can lose output, and the container is gone before it can be read. Start it detached, capture inside the container, and wait:

```bash
cd D:/Sites/autoposter-p49
docker compose -p p49t5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p49t5-suite test \
    sh -c 'pytest -q 2>&1 | tee /app/.superpowers/p49-full-suite.log'
docker wait p49t5-suite
tail -40 .superpowers/p49-full-suite.log
```

Expected: exit code `0` from `docker wait`, and a passing summary line. `tests/test_worker.py` is a known intermittent on full-suite runs (roadmap row 193) — if it and only it fails, re-run that one file alone and record both results; a pass on the second run is the known flake, anything else is this phase's.

- [ ] **Step 2: Count the migration churn from the fixture, do not estimate it**

```bash
python - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("tests/fixtures/collections/golden_port.json").read_text())
for name in ("movies_apply", "shows_apply"):
    state = data[name]["state"]
    derived = [t for t, cell in state.items() if cell.get("sort_title")]
    print("%-14s %3d collection(s), %3d carrying a sort title" %
          (name, len(state), len(derived)))
    print("   separators:", sorted(t for t in state if t.endswith("Collections")))
PY
```

Record both numbers. They are what the PR body states, and they are measured rather than guessed.

- [ ] **Step 3: Pay the README debt**

In `deploy/README.md`, replace the `separators` bullet (`:365-368`) with:

```markdown
- `separators` (default `true`) — maintain a blank "index card" divider
  collection for each GROUP of collections this service manages: `Chart
  Collections`, `Award Collections`, `Ratings Collections` and so on. Each is a
  permanently-empty collection whose sort title floats it above its own block in
  Plex's alphabetised collections tab. Turning it off leaves the sort-title
  prefixes in place and removes only the headings — and an existing heading is
  then an ordinary orphan, reported by the pass and deleted only if
  `delete_unconfigured` is on and `max_deletes` allows it, exactly like any
  other collection this service no longer builds.
- `group_order` (default unset) — reorder those blocks. Unset is the built-in
  order: charts, awards, content ratings, content, location, media, people,
  production, time, and your own `definitions:` entries last. A partial list is
  the normal use — the groups you name lead, in that order, and the rest follow
  behind them:

  ```yaml
  collections:
    group_order: [awards, charts]
  ```

  The valid names are the ten above. An unknown or repeated one is refused at
  config load with the full list, not accepted as a reordering that silently
  did nothing.

**The first pass after this feature ships re-writes one sort title per managed
collection** — one `editSortTitle` PUT each, membership untouched — including
replacing the prefixes on collections adopted from Kometa. Collections this
service does not manage are never touched. The tab reorders once and then
settles. Changing `group_order` later does the same thing again, once.
```

Also update `deploy/README.md:322` region's "Common Sense collections config" heading paragraph only if it claims the divider is the family's — check with `grep -n "Ratings Collections" deploy/README.md` and correct any sentence that still says there is one divider.

In `config/autoposter.example.yaml`, replace the `separators:` line (`:53`) and add the new key beneath it:

```yaml
    separators: true # a blank section-divider collection per collection group
    # group_order: [awards, charts] # reorder the blocks; the rest follow
```

Leave the two commented `sort_title:` examples at `:115` and `:158` exactly as they are — they show an operator writing their own, which still wins over anything derived, and that is the behaviour they document.

- [ ] **Step 4: Update the roadmap — append-shaped**

A parallel phase (prefetch-A) is editing this same file. **Append to each row's existing cell; never rewrite a cell, never reflow a table, never touch a row this phase did not close.** Whichever branch merges second rebases, and both wraps know it.

Row 49 (`:145`) — append to the description cell, before the closing `|`:

```
 **answered 49 (this phase):** delivered — the generalisation shipped.
`collections/groups.py` derives every managed collection's GROUP (the nine
`catalog.CATEGORIES` plus a tenth for the operator's own `definitions:`), its
section number and its sort title; the engine drives one blank divider per
active group (`engine._separators` -> `reconcile.reconcile_separator`, over a
`groups.SeparatorSpec`) where before there was one hand-built constant. The
section numbers are OURS, not transcribed (`.superpowers/sdd/p49-facts.md` C2);
the naming and sort-title FORMULAS are Kometa's and are cited in `groups.py`'s
docstring. `collections.separators` now governs every divider and
`collections.group_order` reorders the blocks. A group that stops having
collections leaves its divider as an ordinary delete-sweep candidate, by design
and tested. Row 93's country-prefixed certification buckets are NOT
retro-fitted here — they remain their own row.
```

Row 5 (`:101`) — the stale `reconcile.py:254` cite. Append:

```
 (cite refreshed row 49: the separator's sort title is no longer a constant at
`reconcile.py:254` — it is derived by `collections/groups.py::separator_sort_title`,
and every managed collection now gets one, not just the divider)
```

- [ ] **Step 5: Re-run the docs-sensitive tests**

```bash
docker compose -p p49t5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test pytest -q tests/test_config_schema.py tests/test_docs_examples.py \
    tests/test_collection_catalog.py 2>&1 | tee .superpowers/p49-t5-docs.log
```

Expected: all pass. If `tests/test_docs_examples.py` does not exist in this tree, run `pytest -q -k "example or readme or docs"` instead and record what it selected.

- [ ] **Step 6: Commit the paperwork**

```bash
git add deploy/README.md config/autoposter.example.yaml \
        docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(collections): collection groups, group_order, and row 49 closed"
```

- [ ] **Step 7: Write the PR body**

`D:\Sites\autoposter\.superpowers\sdd\p49-pr-body.md`. No mention of any tooling that wrote it. It must contain, in this order:

1. **What changed** — one divider became one per group; every managed collection now carries its group's `!<NNN>_` sort-title prefix.
2. **The scheme is ours.** State it plainly: the section numbers are not Kometa's, only the two formulas are, and both are cited. Name `.superpowers/sdd/p49-facts.md` C2.
3. **The churn, with the measured numbers from Step 2.** "N collections in Movies and M in Shows each take one `editSortTitle` PUT on the first pass after deploy. Membership is unchanged. Collections carrying a Kometa prefix that this service manages have it replaced; collections this service does not manage are untouched. The collections tab reorders once and then settles."
4. **The two golden amendments**, each named, each with what its diff gate proved.
5. **The `SEPARATOR_HASH` reproduction** — the function reproduces the shipped constant byte for byte, pinned by `tests/test_collection_group_separators.py::test_separator_hash_reproduces_the_shipped_constant_byte_for_byte`, so no live server takes a spurious re-write of a divider whose desired state did not change.
6. **What is deliberately not here** — the `visible_*`/hub machinery (a different Plex object, Plex Pass, and hubs are not the tab); row 191's forked doubles (touched minimally, not consolidated); row 93's country-prefixed certification buckets.
7. **The probe** — read-only, scrubbed, `docs/research/collection-sort-probe/README.md`, with each of the three questions marked MEASURED or BLOCKED.

- [ ] **Step 8: Write the phase report and tear down**

`D:\Sites\autoposter\.superpowers\sdd\p49-report.md`: the five tasks, what each landed, every commit SHA and subject, the full-suite result, the two golden diff gates and what they proved, the churn numbers, and an explicit note that this branch and prefetch-A both edit the roadmap — **whichever merges second rebases**, and the row-49 and row-5 edits are appends to existing cells so the rebase is a text merge and not a decision.

```bash
docker compose -p p49t5 down
git log --oneline origin/main..HEAD
```

Expected: seven commits — the probe, the groups module, the separators, the separator golden amendment, the sort titles, the sort-title golden amendment, and the docs. `git status` clean, and `git diff --stat origin/main..HEAD` names **no** file under `src/autoposter/providers/`, `src/autoposter/db/`, or `alembic/`.

---

## Self-Review

**Spec coverage.**

- C1 grouping model — Task 2 (`CANONICAL_ORDER`, `preset_groups`, `builtin_group`, `group_for`, the operator group last, `group_order` with its refusal). One new config field, no other surface. ✓ The optional second knob C1 permits (`group_sort_titles`) is **deliberately not added**: `separators: false` removing headings while prefixes stay is already the independent control C1 was hedging against, and a second boolean for the mirror case has no named use. One knob, as C1 preferred.
- C2 probes + NOT_KOMETA scheme — Task 1 (three probes, scrub discipline, evidence in `docs/research/`), the scheme table pinned in this plan and in `groups.py`'s docstring. ✓
- C3 where the derived value enters — Task 4 (`with_derived_sort_title`, out of band, engine-side, in front of `_settings_parts`; three-source precedence; the two traps tested by name). ✓
- C4 separator lifecycle — Task 3 (`separator_hash` reproducing the constant with a pinning test, per-group specs, Kometa's cited naming formula, per-group poster keys with graceful absence, `kind="separator"`, the by-design sweep behaviour tested, the ops/blank refusal tested and worded, the enumeration seam through `groups.separator_titles` into `definition_titles`). ✓
- C5 migration honesty — Tasks 3 and 4 (two own-commit diff-gated amendments) and Task 5 (measured churn in the PR body). ✓ Flagged where it could not hold as literally written.
- C6 boundary — no `visible_*`/`hub_priority` change anywhere; `test_collection_separator.py` touched at call sites only, with a docstring line saying why the fork stays. ✓
- C7 paperwork — Task 5 (row 49 closed, row 5's cite refreshed, README debt paid, roadmap edits append-shaped with the rebase note). ✓
- C8 size — five tasks in the stated order. ✓

**Placeholder scan.** One intentional fill-in remains: `groups.SEPARATOR_POSTER_KEYS` carries the one key on record and is completed from Task 1's measurement, named as such in both places. That is a measurement handoff between tasks, not a "TBD" — Task 2 Step 3 says exactly where the values come from and what a missing one means.

**Type consistency.** `sort_prefix` is the parameter name in all three reconcilers, on `SmartContext`, and in `_run_one`; `groups.sort_prefix()` returns it and `groups.sort_prefix_for()` resolves it from a definition. `SeparatorSpec`'s five fields are constructed in exactly one place (`groups.separator_specs`) and read in exactly one (`reconcile.reconcile_separator`). `separator_hash(title, summary, sort_title)` has one signature across the module, the pinning test and the driver. `reconcile.SEPARATOR_TITLE`/`SEPARATOR_SUMMARY` survive with their shipped values (now derived); `SEPARATOR_SORT_TITLE` and `SEPARATOR_HASH` are removed in Task 3 and every reader is updated in the same task.
