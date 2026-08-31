# Search Tails 1 — the Cheap Tail and the Award Closures — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close roadmap rows 170 and 172 (seven new search attributes — `title`, `edition`, and the five media booleans — every cell transcribed and oracle-pinned), close the person-rows golden gap the search oracle still carries, close rows 183/149/150 as paperwork, and sweep the rot the last three phases left behind (stale `imdb_lists.py` mentions, six locations citing bare `task-N-report.md` paths that now resolve to the wrong phase's reports).

**Architecture:** Three tasks. Task 1 appends the seven `FILTER_ATTRIBUTES` rows and pins them with two new Kometa-string oracle configs (18 movie / 19 show), RED-first through the in-repo driver, with the table's checksum comment and totals tests updated by measurement. Task 2 pins the phase-B person rows with two more oracle configs (20/21 — proven falsifiable by mutation, since the rows already exist), then does the rot sweep (each pointer verified against the archive before it is re-pointed) and the five row closes. Task 3 wraps: row 101's family note records the tail at 22, the PR body is written gitignored, both full suites are stated then measured. NO push, NO PR.

**Tech Stack:** Python 3.14 + pytest via the compose `test` service; the in-repo Kometa v2.4.8 transcription (`tests/oracle/9b/kometa_build_filter.py`) as the oracle driver; vitest via the compose `web` service for the wrap's frontend run.

**Binding requirements:** `.superpowers/sdd/p-tails1-facts.md` (C1–C4, adjudicated 2026-08-31). Where this plan and that file could ever disagree, that file wins.

## Global Constraints

Every task's requirements implicitly include this section.

1. **Scope (facts C1).** Rows IN: 170 (`title` + `edition`), 172 (the five media booleans), 183 (no-op close), 149 + 150 (title-closes, residual notes kept), the person-rows oracle-config gap, and the rot sweep (the three surviving `imdb_lists.py` mentions at roadmap :264/:671 plus the bare `task-N-report.md` pointers in rows 148/149/150/151/153 and `builders/imdb_search.py:26`). OUT: 177/176 (probe-first — Phase 2), 151/153's open halves (the cache design — Phase 2), 148 (the constraint vocabularies — Phase 2 or its own), 173/179 (blocked/design). No fetch, no probe, no design: every semantic this phase ships is already transcribed in-repo.
2. **The law (facts C2).** Every new row's semantics come FROM the transcription — the row's note cites the `tests/oracle/9b/kometa_build_filter.py` line (fetched-not-recalled holds because the transcription IS the fetched artifact). Every new row rides a golden config proven against the oracle driver, RED-first. The checksum comments (`filters.py:547-556`) and the totals tests are updated by MEASUREMENT — run the suite, read the failure, confirm it matches this plan's predicted numbers; if measurement and prediction disagree, STOP and reconcile the table, never the test. Never edit a pinned golden to make a test pass: if ours differs, ours is wrong.
3. **Container discipline (the standing recipe).** Unique compose project per task (`ptl11`, `ptl12`, `ptl13`), always with the `.superpowers/isolated-db.yml` overlay for the `test` service, always `sh -c` with tee to a path under `/app/.superpowers/` inside the container — never rely on streamed stdout (it is filtered, and `--rm` deletes the container before it can be re-read). A long run (the full suite) uses **no `--rm`**, is started detached with `run -d --name <project>-<label>`, is waited on from the host, and the log is read from the host. Teardown is `docker compose -p <project> down` — **never** `down -v`. Two pytest commands against the same compose project are unsafe — keep unique `-p` names per task.
4. **RED-first.** Task 1's new tests are RED naturally (the rows do not exist yet; the oracle refuses `title` by name). Task 2's oracle configs land against rows that already exist, so their red is manufactured: a temporary mutation of the exact cell the config guards (run red, restore, run green). A guard never seen red proves nothing.
5. **Timeouts (sweep 4's standing derivation).** Full backend suite: `timeout -s KILL 2700` (worst measured full run 797.53s, `run-p-sweep4-full.log`). Targeted files: `timeout -s KILL 600`.
6. **Commits.** Conventional messages, `--no-gpg-sign`, staged **by name** (never `git add -A`). No `Co-Authored-By`, no AI attribution of any kind, in commits or the PR body.
7. **Artifacts.** Phase artifacts under `.superpowers/sdd/` use the `p-tails1-` prefix; run logs are `.superpowers/run-p-tails1-*.log`. The PR body goes to `.superpowers/sdd/p-tails1-pr-body.md` — `.superpowers/` is gitignored and the file stays uncommitted. **Push and PR creation are NOT in this plan**: the user gates the PR.
8. **Branch (facts C4).** `feat/search-tails-1` from `chore/sweep-4`'s tip (286456b = pending PR #109's content) — **FOURTH in the stacked merge order** (main ← the #103-era history ← #108 ← #109 ← this): when #109 rebases onto a rebased #108, this branch rebases onto the rebased #109, and the wrap's PR body says so. Verified with a **content probe** (never a sha probe): `src/autoposter/collections/imdb_graphql.py` must exist on the cut branch — that file exists only in #109's content (sweep 4's row-152 rename). Every line number in this plan was verified against 286456b.
9. **Paperwork (facts C3).** Rows 170/172/183/149/150 close; the search-tail count in row 101's family notes updates (29 → 22); the PR body plain.

## Adjudications Made by This Plan

Decisions the facts file left open, settled here so no executor has to guess.

- **Two tails configs, split by LIBTYPE, not by row.** Facts C1 says "170/172 each get a golden config". One config per row cannot pin both libtype columns (a `str` row's movie field and its show rescope cannot share a config), while one config per LIBTYPE pins **all thirteen** field×libtype cells of the seven rows at once: config 18 (movie) carries `title`, `edition`, and all five booleans (`duplicate` is legal only there); config 19 (show) carries `title`, `edition`, and the four show-legal booleans. Each of rows 170 and 172 is exercised by both configs, which satisfies and exceeds the facts' per-row phrasing. Precedent: config 16 mixed `decade` and `country` on one query.
- **The person-rows gap takes TWO configs (20 and 21), not one.** Facts C1 says "one config exercising the phase-B person rows (`show.actor` rescoping + a movie-only crew row)" — but a single config cannot legally hold both: `director` on a show library is refused by Kometa's own `movie_only_searches` (the driver would raise, not render). Config 20 (show) pins `show.actor`; config 21 (movie) pins `actor`'s bare field beside `director`. The smallest legal set that pins both named semantics.
- **`edition.kinds = ("movie",)`, stated as inferred.** The row is dual-vocabulary (`filterable=True`) on the in-repo evidence — `edition` sits in Kometa's `string_filters`, verbatim at `.superpowers/oracle/9a/kometa_oracle.py:56` (transcribing builder.py:377-447), and in neither the 44 filter-only nor the 29 search-only lists (the 9b plan's derivation, `docs/superpowers/plans/2026-08-26-phase9b-search-dsl.md:270-285`) — but its per-library FILTER scope has no in-repo transcription (`builder.filters_by_type` is still untranscribed — phase B review D11, whose standing remedy is "transcribe or soften to inferred whenever 9b's oracle is next extended"). The row's note marks the `kinds` cell INFERRED (an edition is a movie concept; upstream documents the filter for movie libraries); the consequence either way is a refusal, since the row ships no accessor at the `unprobed` tier. `title.kinds = _BOTH` (a title filter applies to every library type — the one universally-scoped name in Kometa's own vocabulary). The SEARCH columns of both rows are fully transcribed and cited by line.
- **"The five bare `task-N-report.md` pointers" is read as the five ROWS.** Rows 148/149/150/151/153 carry **nine** bare pointer instances between them (149 has three, 150 and 151 two each); all nine are re-pointed, plus `builders/imdb_search.py:26`. Every target was verified at plan time against the archive's actual content (section numbers and topics listed in Task 2), and each step re-verifies with a grep before editing — the row-83 precedent.
- **`filters.py:1520`'s "today's twenty-one rows" is corrected to thirty-three in Task 1.** The count was already stale (the table holds twenty-six today) and this phase changes the row count again; the comment's load-bearing claim — every row is searchable, so the filter-only branch is unreachable — was re-verified and still holds at thirty-three (all seven new rows carry search fields). One count, same file, same commit as the table edit. Nothing else stale is touched.
- **Test counts expected at the wrap:** backend 4184 → **4190** (+2 oracle params in T1, +2 unit tests in T1, +2 oracle params in T2); frontend unchanged at **358** (no frontend edit anywhere in this plan). Stated here so Task 3 states-then-measures rather than measures-then-nods.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `docs/superpowers/plans/2026-08-31-search-tails-1.md` | **Commit as-is** (this plan) | T1 |
| `tests/oracle/9b/kometa_build_filter.py` | **Modify.** CONFIGS tail (after :1055): configs 18/19 (T1), 20/21 + two CHOICES entries (T2). The transcription tables themselves are NEVER touched | T1, T2 |
| `tests/test_collection_search_oracle.py` | **Modify.** Docstring config-count + history sections; CONFIGS (:132-185); KOMETA (:196-214); CHOICES copy (:110-122, T2) | T1, T2 |
| `src/autoposter/collections/filters.py` | **Modify.** Module docstring counts (:111-121); checksum comment (:545-556 + one new paragraph); seven rows appended after `user_rating` (:1109); the stale count at :1520 | T1 |
| `tests/test_collection_filters.py` | **Modify.** Row list (:88-115); column totals (:158-212); search_kinds counter (:339-341); the renamed twentyfive test (:348-375); two new tests after :511 | T1 |
| `src/autoposter/collections/builders/imdb_search.py` | **Modify** (:26). Archive re-point | T2 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modify.** Rot sweep (:180-185, :264, :671); row closes (:266, :268, :279, :181, :182); row 101's family note (:203) | T2, T3 |
| `.superpowers/sdd/p-tails1-pr-body.md` | **Create** (gitignored, never committed) | T3 |

**Interfaces (whole-plan):** Task 1 produces the seven `FilterAttribute` rows (names: `title`, `edition`, `hdr`, `dovi`, `trash`, `duplicate`, `unmatched`) appended in exactly that order — Task 1's totals-test edits and Task 3's row-101 note both depend on that order and on the counts derived from it. Tasks consume nothing else from each other beyond commits being present.

---

### Task 1: The Seven Rows and Their Goldens

**Files:**
- Modify: `tests/oracle/9b/kometa_build_filter.py` (CONFIGS list, tail at :1044-1056)
- Modify: `tests/test_collection_search_oracle.py` (:10, :86-96 region, :132-185, :196-214)
- Modify: `src/autoposter/collections/filters.py` (:111-121, :545-556, after :1109, :1520)
- Test: `tests/test_collection_filters.py` (:88-115, :158-212, :339-341, :348-375, new tests after :511)

**Interfaces:**
- Consumes: the existing table machinery — `FilterAttribute` (filters.py:459-540), `SEARCH_OPERATORS_BY_TYPE` (:255-292, `str` = the six string operators, `bool` = `("eq",)`), `field_for` (:524-540). No engine change anywhere: the `bool` renderer and the string branch both ship since 9b.
- Produces: `BY_NAME["title"]`, `BY_NAME["edition"]` (type `str`, source `unprobed`, `filterable=True`), `BY_NAME["hdr"|"dovi"|"trash"|"duplicate"|"unmatched"]` (type `bool`, source `search-only`, `filterable=False`); oracle configs `18-the-tails-on-a-movie`, `19-the-tails-on-a-show`. Task 2 appends configs 20/21 after these; Task 3 cites the resulting counts (33 of 55 searchable, tail 22).

- [ ] **Step 1: Preflight, branch, commit the plan**

Working tree must be clean and on `chore/sweep-4`:

```bash
git status --porcelain            # expect: empty (this plan file may show untracked -- fine)
git log --oneline -1              # expect: 286456b chore(sweep-4): polish pass ...
test -f src/autoposter/collections/imdb_graphql.py && echo CONTENT-PROBE-OK
```

Expected: `CONTENT-PROBE-OK` — the content probe for #109's content (the sweep-4 rename exists only there). Never probe by sha alone. Then:

```bash
git checkout -b feat/search-tails-1
git add docs/superpowers/plans/2026-08-31-search-tails-1.md
git commit --no-gpg-sign -m "docs(plans): search tails 1 -- the cheap tail and the award closures"
```

- [ ] **Step 2: Append configs 18/19 to the ORACLE DRIVER**

In `tests/oracle/9b/kometa_build_filter.py`, inside the `CONFIGS` list, immediately after the config-17 entry (`("show", {"all": {"country": "France"}}),` at :1055) and before the closing `]`, append:

```python
    # 18: the search-tails-1 rows on a movie library -- the text pair through
    # the STRING branch (``title`` on its bare field; ``edition.begins``
    # through search_translation's ``editionTitle``, plex.py:64 here at :86)
    # and all five media booleans on their bare movie fields, ``duplicate``
    # legal here alone (movie_only_searches, :280).
    ("movie", {"all": {
        "title": "Dune",
        "edition.begins": "Director",
        "hdr": True,
        "dovi": False,
        "trash": False,
        "duplicate": True,
        "unmatched": False,
    }}),
    # 19: the same rows on a SHOW library, which is what a movie config cannot
    # reach: ``title`` -> ``show.title`` (:164), ``edition`` -> ``editionTitle``
    # -> ``show.editionTitle`` (:86 then :180 -- both translation tables
    # composed), ``hdr``/``dovi``/``trash`` to the EPISODE libtype (:182-186)
    # and ``unmatched`` to ``show.unmatched`` (:173). ``duplicate`` is
    # movie-only and absent by law.
    ("show", {"all": {
        "title.isnot": "Dune",
        "edition.ends": "Cut",
        "hdr": True,
        "dovi": False,
        "trash": True,
        "unmatched": False,
    }}),
```

Touch NOTHING else in the driver — the transcription tables already carry every attribute these configs name (`string_attributes` :353, `boolean_attributes` :355-372), which is the whole reason no driver change beyond CONFIGS is legal.

- [ ] **Step 3: Predict, then run the driver**

The predictions, derived from the transcription before the driver runs (the 10a-1 precedent — predicted at the step, then confirmed):

```
18 ?type=1&sort=titleSort&title=Dune&and=1&editionTitle%3C=Director&and=1&hdr=1&and=1&dovi!=1&and=1&trash!=1&and=1&duplicate=1&and=1&unmatched!=1
19 ?type=2&sort=titleSort&show.title!%3D=Dune&and=1&show.editionTitle%3E=Cut&and=1&episode.hdr=1&and=1&episode.dovi!=1&and=1&episode.trash=1&and=1&show.unmatched!=1
```

(Derivation keys: bare string modifier renders empty — `title=Dune`; `.begins` → `%3C`, `.ends` → `%3E`, `.isnot` → `!%3D` — config 10/15 precedents; a `True` boolean renders `field=1`, `False` renders `field!=1` — config 9 precedent; terms under `all` join with `and=1`.)

Run the driver in the container:

```bash
docker compose -p ptl11 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "python tests/oracle/9b/kometa_build_filter.py 2>&1 | tee /app/.superpowers/run-p-tails1-t1-driver.log"
```

Expected: nineteen lines; lines 1-17 byte-identical to the pinned `KOMETA` strings; lines 18/19 matching the predictions above. **If a driver line differs from the prediction, the DRIVER wins** — adopt its string and record the divergence in the oracle docstring section (Step 4). Do not touch the driver's tables to "fix" a surprise.

- [ ] **Step 4: Pin configs 18/19 in the oracle test**

In `tests/test_collection_search_oracle.py`:

(a) Docstring header (:10): `Seventeen configs, and seventeen URI strings` → `Nineteen configs, and nineteen URI strings`.

(b) After the `## The sixteenth and seventeenth configs` section (ends :95), add:

```
## The eighteenth and nineteenth configs

Search-tails-1 (roadmap rows 170 + 172) added seven rows -- ``title``,
``edition`` and the five media booleans -- and, as with 16/17, no existing
golden would have reached any of them: ``test_the_configs_cover_every_shipped_
value_type`` covers TYPES, and ``str``/``bool`` were already exercised. The
two configs split by LIBTYPE so that all thirteen field-by-libtype cells are
pinned: config 18 is the movie column (the bare ``title`` field,
``editionTitle`` through search_translation, the five booleans' bare fields
with ``duplicate`` legal only here), config 19 the show column (``show.title``,
the doubly-translated ``show.editionTitle``, the episode-libtype
``hdr``/``dovi``/``trash``, and ``show.unmatched``). Both goldens came from
the same driver in the same way -- predicted from the transcription first,
then confirmed by running it.
```

(c) `CONFIGS` (after the `17-country-on-a-show` entry, :184):

```python
    ("18-the-tails-on-a-movie", "movie", {"all": {
        "title": "Dune",
        "edition.begins": "Director",
        "hdr": True,
        "dovi": False,
        "trash": False,
        "duplicate": True,
        "unmatched": False,
    }}),
    ("19-the-tails-on-a-show", "show", {"all": {
        "title.isnot": "Dune",
        "edition.ends": "Cut",
        "hdr": True,
        "dovi": False,
        "trash": True,
        "unmatched": False,
    }}),
```

(d) `KOMETA` (after the `17-country-on-a-show` pin, :213) — the strings **as the driver printed them** in Step 3:

```python
    "18-the-tails-on-a-movie": "?type=1&sort=titleSort&title=Dune&and=1&editionTitle%3C=Director&and=1&hdr=1&and=1&dovi!=1&and=1&trash!=1&and=1&duplicate=1&and=1&unmatched!=1",
    "19-the-tails-on-a-show": "?type=2&sort=titleSort&show.title!%3D=Dune&and=1&show.editionTitle%3E=Cut&and=1&episode.hdr=1&and=1&episode.dovi!=1&and=1&episode.trash=1&and=1&show.unmatched!=1",
```

(e) The `KOMETA` provenance comment (:190-195) — after "sixteen and seventeen, ... same as the fifteen before them" append: `, and search-tails-1's plan (eighteen and nineteen, predicted from the transcription, then confirmed by the driver -- Task 2 adds twenty and twenty-one the same way)`.

- [ ] **Step 5: Write the two failing unit tests**

In `tests/test_collection_filters.py`, after `test_country_is_rescoped_for_a_show_library_and_decade_refuses_one` (ends :511), add:

```python
def test_the_text_rows_take_the_string_operators_and_rescope():
    """Row 170's two rows. ``title`` is the bare field re-scoped to
    ``show.title`` (kometa_build_filter.py:164); ``edition`` is the one row in
    the table that composes BOTH translation tables -- search_translation's
    ``editionTitle`` (:86), then show_translation's entry for the TRANSLATED
    name (:180). Both are dual-vocabulary on ``unprobed`` (the ``country``
    pattern): a ``filters:`` block parses the key and refuses at the accessor
    by naming the tier."""
    from autoposter.collections.filter_values import (
        AttributeNotInListing,
        PlexItemView,
    )

    for name in ("title", "edition"):
        row = BY_NAME[name]
        assert row.type == "str", name
        assert row.source == "unprobed", name
        assert row.filterable, name
        assert row.search_operators == (
            "contains", "not", "is", "isnot", "begins", "ends",
        ), name

    assert BY_NAME["title"].kinds == ("movie", "show")
    assert BY_NAME["title"].field_for("movie") == "title"
    assert BY_NAME["title"].field_for("show") == "show.title"
    assert BY_NAME["edition"].kinds == ("movie",)
    assert BY_NAME["edition"].field_for("movie") == "editionTitle"
    assert BY_NAME["edition"].field_for("show") == "show.editionTitle"

    parse_filters({"title": "Dune"})
    with pytest.raises(AttributeNotInListing, match="unprobed"):
        PlexItemView(object()).get("title")


def test_the_media_booleans_are_search_only_and_libtype_gated():
    """Row 172's five rows. ``duplicate`` is movie-only (movie_only_searches,
    kometa_build_filter.py:280), so a show library refuses it BY NAME;
    ``hdr``/``dovi``/``trash`` re-scope to the EPISODE libtype on a show
    library (:182-186) exactly as ``resolution`` does, and ``unmatched`` to
    ``show.unmatched`` (:173) -- the SHOW level, because a match belongs to
    the item and not the file. All five are ``search-only``: Kometa has no
    filter of any of these names (row 96's 29-name search-only list), so a
    ``filters:`` block refuses by pointing at the search block."""
    for name in ("hdr", "dovi", "trash", "duplicate", "unmatched"):
        row = BY_NAME[name]
        assert row.type == "bool", name
        assert row.source == "search-only", name
        assert not row.filterable, name
        assert row.search_operators == ("eq",), name

    assert BY_NAME["hdr"].field_for("show") == "episode.hdr"
    assert BY_NAME["dovi"].field_for("show") == "episode.dovi"
    assert BY_NAME["trash"].field_for("show") == "episode.trash"
    assert BY_NAME["unmatched"].field_for("show") == "show.unmatched"
    assert BY_NAME["duplicate"].field_for("movie") == "duplicate"
    with pytest.raises(ValueError, match="not searchable on a show library"):
        BY_NAME["duplicate"].field_for("show")

    with pytest.raises(ValueError, match="plex_search attribute"):
        parse_filters({"hdr": True})
```

(`BY_NAME`, `parse_filters`, and `pytest` are already module-level imports in this file — check the import block and add only what is missing.)

- [ ] **Step 6: Run the targeted files — verify RED**

```bash
docker compose -p ptl11 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "timeout -s KILL 600 pytest tests/test_collection_search_oracle.py tests/test_collection_filters.py 2>&1 | tee /app/.superpowers/run-p-tails1-t1-red1.log"
```

Expected: exactly **4 failures**, everything else green —
- `test_our_url_is_byte_identical_to_kometas[18-the-tails-on-a-movie]` and `[19-the-tails-on-a-show]`: `ValueError: unknown search attribute 'title' at params...`
- the two new unit tests: `KeyError: 'title'` / `KeyError: 'hdr'`

and `test_the_driver_still_produces_the_pinned_strings` **green** — the driver already produces the pinned 18/19 strings, which is the proof the goldens are Kometa's before our side can render them at all. That asymmetry (driver green, ours red) is this task's RED.

- [ ] **Step 7: Append the seven rows to `FILTER_ATTRIBUTES`**

In `src/autoposter/collections/filters.py`, replace the table's closing (the `user_rating` row's last lines plus the tuple's closing paren, :1107-1110):

```python
        search_field="userRating", show_search_field="show.userRating",
        search_kinds=_BOTH, filterable=True,
    ),
)
```

with:

```python
        search_field="userRating", show_search_field="show.userRating",
        search_kinds=_BOTH, filterable=True,
    ),
    # --- rows search-tails-1 added -------------------------------------------
    #
    # Rows 170 (``title``, ``edition``) and 172 (the five media booleans),
    # appended rather than interleaved like every batch before them. Every
    # SEARCH cell is cited to the repo's own verbatim transcription of
    # Kometa's tables -- tests/oracle/9b/kometa_build_filter.py, the fetched
    # artifact -- by LINE, and oracle configs 18/19 pin every field-by-libtype
    # render against the driver itself.
    FilterAttribute(
        "title", "str", _BOTH, "unprobed",
        "The item's own title -- a string_attribute upstream "
        "(kometa_build_filter.py:353, transcribing plex.py:507), so it takes "
        "the six string modifiers exactly as `studio` does: a bare "
        "`title: Dune` is a case-insensitive SUBSTRING match, not an exact "
        "one. No search_translation entry, so the movie field is the bare "
        "`title`; re-scoped to `show.title` on a show library by "
        "show_translation (kometa_build_filter.py:164). Dual-vocabulary like "
        "`country`: Kometa FILTERS on `title` too (`string_filters` -- the 9a "
        "oracle's verbatim transcription of builder.py:377-447 opens with "
        "it), so `filterable` is True -- and `unprobed` for the reason "
        "`country` carries that tier: 9a's probe never asked about a listing "
        "accessor for it, so a `filters:` block parses the key and refuses "
        "at the accessor by naming this tier. Kometa's separate `show_title` "
        "FILTER (season/episode level, one of the 44 filter-only names) is a "
        "different attribute and is NOT this row.",
        search_field="title", show_search_field="show.title",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "edition", "str", ("movie",), "unprobed",
        "Plex's edition title. The search field is `editionTitle` via "
        "search_translation (kometa_build_filter.py:86), then "
        "`show.editionTitle` on a show library via show_translation's entry "
        "for the TRANSLATED name (kometa_build_filter.py:180) -- the one row "
        "in the table that composes both tables, which is why oracle config "
        "19 pins the show render. Dual-LISTED in `searches` exactly like "
        "`studio` -- a string_attribute (:353) AND a tag_attribute (:419) -- "
        "and for `studio`'s own reason every operator this table ships takes "
        "the STRING branch: the value goes to Plex quoted and unresolved. "
        "Dual-VOCABULARY like `country` (`edition` is in Kometa's "
        "`string_filters`, the 9a oracle's transcription of "
        "builder.py:377-447), so `filterable` is True on `unprobed`. `kinds` "
        "is movie-only: an edition is a movie concept and upstream scopes "
        "the FILTER to movie libraries -- stated as INFERRED, not "
        "transcribed, because `builder.filters_by_type` still has no in-repo "
        "transcription (phase B review D11, unchanged here); the consequence "
        "either way is a refusal, since no accessor ships at this tier. The "
        "SEARCH column is the transcribed one and answers both library "
        "types.",
        search_field="editionTitle", show_search_field="show.editionTitle",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "hdr", "bool", _BOTH, "search-only",
        "High dynamic range, a server-side boolean: `hdr: true` emits "
        "`hdr=1`, `false` emits `hdr!=1` -- the `unplayed` row documents the "
        "bool rendering once for the whole type. In boolean_attributes "
        "(kometa_build_filter.py:357); no search_translation entry, so the "
        "movie field is the bare `hdr`; re-scoped to the EPISODE libtype on "
        "a show library (`episode.hdr`, kometa_build_filter.py:182) -- the "
        "same re-scoping `resolution` already does, because HDR is a "
        "property of the FILE and a show's files are its episodes'. "
        "`search-only`: Kometa has no `hdr` FILTER (row 96's 29-name "
        "search-only list), so a `filters:` block refuses it by naming the "
        "block it does belong to.",
        search_field="hdr", show_search_field="episode.hdr",
        search_kinds=_BOTH, filterable=False,
    ),
    FilterAttribute(
        "dovi", "bool", _BOTH, "search-only",
        "Dolby Vision. The one boolean with a search_translation entry, and "
        "it is the IDENTITY (kometa_build_filter.py:108: `dovi` -> `dovi`) -- "
        "transcribed as such rather than skipped; re-scoped to "
        "`episode.dovi` on a show library (kometa_build_filter.py:183). In "
        "boolean_attributes at :356. `search-only`: Kometa's client-side "
        "vocabulary has `has_dolby_vision` -- a DIFFERENT name, one of the "
        "44 filter-only ones -- and no `dovi` filter, so `filters:` refuses "
        "this spelling by pointing at the search block.",
        search_field="dovi", show_search_field="episode.dovi",
        search_kinds=_BOTH, filterable=False,
    ),
    FilterAttribute(
        "trash", "bool", _BOTH, "search-only",
        "Plex's trash flag: an item whose file has gone missing but has not "
        "yet been emptied from the library. In boolean_attributes "
        "(kometa_build_filter.py:362); no search_translation entry, so the "
        "movie field is the bare `trash`; re-scoped to `episode.trash` on a "
        "show library (kometa_build_filter.py:186) -- file-level, like "
        "`hdr`/`dovi`. `search-only`: Kometa has no filter of this name.",
        search_field="trash", show_search_field="episode.trash",
        search_kinds=_BOTH, filterable=False,
    ),
    FilterAttribute(
        "duplicate", "bool", ("movie",), "search-only",
        "Items carrying more than one media version. MOVIE-ONLY -- it is in "
        "movie_only_searches (kometa_build_filter.py:280), like `unplayed` "
        "-- so a show library refuses it BY NAME rather than being sent a "
        "query Plex answers with the wrong set; the show-side spelling is "
        "`episode_duplicate`, one of family E's twenty (roadmap row 173), "
        "deliberately not aliased. In boolean_attributes at :359. "
        "`search-only`: Kometa has no filter of this name (its `versions` "
        "filter counts media items and is one of the 44 filter-only "
        "names).",
        search_field="duplicate", show_search_field=None,
        search_kinds=("movie",), filterable=False,
    ),
    FilterAttribute(
        "unmatched", "bool", _BOTH, "search-only",
        "Items with no agent match. In boolean_attributes "
        "(kometa_build_filter.py:358); no search_translation entry, so the "
        "movie field is the bare `unmatched`; re-scoped to `show.unmatched` "
        "on a show library (kometa_build_filter.py:173) -- the SHOW level, "
        "not the episode's, unlike `hdr`/`dovi`/`trash`: a match belongs to "
        "the ITEM, not the file. `show_unmatched` and `episode_unmatched` "
        "remain family E's separate names (roadmap row 173), not aliases of "
        "this row. `search-only`: Kometa has no filter of this name.",
        search_field="unmatched", show_search_field="show.unmatched",
        search_kinds=_BOTH, filterable=False,
    ),
)
```

- [ ] **Step 8: Update the module docstring counts and the checksum comment**

Still in `filters.py`, three edits:

(a) The docstring paragraph at :111-121. Replace:

```
This table covers **26** of the 55 search names and **23** of the 70 filter
names. Both halves of the residue are real work, and they are different work:
the 47 unfiltered names are roadmap row 96's remainder (9a left 55 of them;
``plays``, ``last_played``, 10a's ``country`` and phase B's four people rows
came in here as ``unprobed``, which is a source tier and not an accessor, so
the row-96 arithmetic moved by seven and no further -- and then phase B's own
probe gave ``plays`` and ``last_played`` real listing accessors and appended
``user_rating`` with one, so those three are the first names counted here that
an operator can actually filter on rather than merely find in the table), while
the 29 unsearched names are 9b's own tail, filed per family for T6. The two
must not be reported as one number, which is what row 96's original "~45" did.
```

with:

```
This table covers **33** of the 55 search names and **25** of the 70 filter
names. Both halves of the residue are real work, and they are different work:
the 45 unfiltered names are roadmap row 96's remainder (9a left 55 of them;
``plays``, ``last_played``, 10a's ``country``, phase B's four people rows and
search-tails-1's ``title``/``edition`` came in here as ``unprobed``, which is
a source tier and not an accessor, so the row-96 arithmetic has moved by nine
in total -- and then phase B's own probe gave ``plays`` and ``last_played``
real listing accessors and appended ``user_rating`` with one, so those three
are the first names counted here that an operator can actually filter on
rather than merely find in the table), while the 22 unsearched names are 9b's
own tail, filed per family for T6 -- search-tails-1 took seven of them (rows
170 + 172: the text pair, which also moved the filter-covered count by two,
and the five media booleans, which are search-only and move no filter number).
The two must not be reported as one number, which is what row 96's original
"~45" did.
```

(b) The checksum comment at :547-556. Replace:

```
# Twenty-six rows: 9a's fifteen in the order the roadmap names them
# (roadmap.md:538-551), then 9b's four, 10a's two and phase B's five appended
# rather than interleaved so the first fifteen still read against the roadmap
# line they came from. Column totals are asserted in
# tests/test_collection_filters.py as the transcription's checksum:
# 13 tag / 1 str / 3 int / 3 float / 3 date / 1 duration / 2 bool;
# 12 listing / 5 tier2-batched / 1 tier2-deferred / 5 unprobed / 3 search-only;
# 15 both-kinds / 10 movie-only / 1 show-only for ``kinds``, and
# 18 / 7 / 1 for ``search_kinds``, which is a different split and that is the
# point of the second column.
```

with:

```
# Thirty-three rows: 9a's fifteen in the order the roadmap names them
# (roadmap.md:538-551), then 9b's four, 10a's two, phase B's five and
# search-tails-1's seven appended rather than interleaved so the first
# fifteen still read against the roadmap line they came from. Column totals
# are asserted in tests/test_collection_filters.py as the transcription's
# checksum:
# 13 tag / 3 str / 3 int / 3 float / 3 date / 1 duration / 7 bool;
# 12 listing / 5 tier2-batched / 1 tier2-deferred / 7 unprobed / 8 search-only;
# 20 both-kinds / 12 movie-only / 1 show-only for ``kinds``, and
# 24 / 8 / 1 for ``search_kinds``, which is a different split and that is the
# point of the second column.
```

Then, after the phase-B source-split paragraph (ends at the "each argued on its own row rather than by the tier they share." line, :577) and before the `# THE PROBE` paragraph (:579), insert:

```
#
# Search-tails-1 appended SEVEN: rows 170 and 172. ``title`` and ``edition``
# are the second and third ``str`` rows, dual-vocabulary on ``unprobed`` (the
# ``country`` shape), moving ``str`` by two, ``unprobed`` by two and the
# filter-covered count by two. The five media booleans -- ``hdr``, ``dovi``,
# ``trash``, ``duplicate``, ``unmatched`` -- are ``search-only`` like
# ``decade`` (Kometa has no filter of any of these names), moving ``bool`` by
# five, ``search-only`` by five and no filter number at all. Every search
# cell is cited to tests/oracle/9b/kometa_build_filter.py by line in its own
# row's note, and oracle configs 18/19 pin every field-by-libtype render.
```

(c) The stale count at :1520: `# The FIRST of the two branches is unreachable with today's twenty-one rows:` → `# The FIRST of the two branches is unreachable with today's thirty-three rows:` (the claim itself — every row is searchable — was re-verified for the seven new rows: all carry search fields).

- [ ] **Step 9: Run again — new tests green, totals tests red by exactly the predicted amounts**

```bash
docker compose -p ptl11 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "timeout -s KILL 600 pytest tests/test_collection_search_oracle.py tests/test_collection_filters.py 2>&1 | tee /app/.superpowers/run-p-tails1-t1-red2.log"
```

Expected: the four Step-6 failures now PASS; the failures remaining are the totals guards doing their job —
- `test_the_table_holds_exactly_the_tier_one_rows` (seven names missing from the pinned list)
- `test_the_column_totals_are_the_transcriptions_checksum` (str 1→3, bool 2→7, unprobed 5→7, search-only 3→8)
- `test_the_search_kinds_column_is_its_own_and_differs_from_kinds` (18/7/1 → 24/8/1)
- `test_every_row_is_searchable_and_twentythree_are_filterable` (26→33, 23→25)

**Read each failure and confirm the measured numbers equal this plan's predictions. If any differs, STOP: the table is wrong (a mistyped cell), not the test.** This measured red is what facts C2 means by "updated by measurement".

- [ ] **Step 10: Update the totals tests to the measured values**

In `tests/test_collection_filters.py`:

(a) `test_the_table_holds_exactly_the_tier_one_rows` — append to the pinned list after `"user_rating",`:

```python
        "title",
        "edition",
        "hdr",
        "dovi",
        "trash",
        "duplicate",
        "unmatched",
    ]
```

and append to its docstring: `Search-tails-1 appended seven the same way: ``title`` and ``edition`` (row 170, dual-vocabulary on unprobed) and the five media booleans (row 172, search-only; ``duplicate`` movie-only per movie_only_searches).`

(b) `test_the_column_totals_are_the_transcriptions_checksum`:

```python
    assert {k: len(v) for k, v in by_type.items()} == {
        "tag": 13,
        "str": 3,
        "int": 3,
        "float": 3,
        "date": 3,
        "duration": 1,
        "bool": 7,
    }
```

```python
    assert by_source["unprobed"] == [
        "country", "actor", "director", "writer", "producer", "title", "edition",
    ]
```

```python
    assert by_source["search-only"] == [
        "unplayed", "progress", "decade",
        "hdr", "dovi", "trash", "duplicate", "unmatched",
    ]
```

Extend the comment block above the `unprobed` assertion (after the phase-B paragraph) with: `# Search-tails-1 appended ``title`` and ``edition`` here for the same reason again: Kometa filters on both (string_filters) and no probe has ever asked about a listing accessor for either.`

(c) `test_the_search_kinds_column_is_its_own_and_differs_from_kinds`:

```python
    assert Counter(row.search_kinds for row in FILTER_ATTRIBUTES) == {
        ("movie", "show"): 24, ("movie",): 8, ("show",): 1,
    }
```

(d) Rename `test_every_row_is_searchable_and_twentythree_are_filterable` → `test_every_row_is_searchable_and_twentyfive_are_filterable`, update:

```python
    assert all(row.searchable for row in FILTER_ATTRIBUTES)
    assert len(SEARCHABLE_ATTRIBUTES) == 33
    assert len(FILTERABLE_ATTRIBUTES) == 25
    assert set(SEARCHABLE_ATTRIBUTES) - set(FILTERABLE_ATTRIBUTES) == {
        "unplayed", "progress", "decade",
        "hdr", "dovi", "trash", "duplicate", "unmatched",
    }
```

and append to its docstring: `Search-tails-1 added ``title`` and ``edition`` (both vocabularies, so both counts moved by two) and the five media booleans (search-only, so only the searchable count moved by five -- they join unplayed/progress/decade in the difference set).`

- [ ] **Step 11: Run the three table suites — verify green**

```bash
docker compose -p ptl11 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "timeout -s KILL 600 pytest tests/test_collection_search_oracle.py tests/test_collection_filters.py tests/test_collection_filter_values.py 2>&1 | tee /app/.superpowers/run-p-tails1-t1-green.log"
```

Expected: all pass, 0 failures. (`test_collection_filter_values.py` derives its tier sets from the table, so it must stay green with no edit — if it reds, a new row claimed an accessor tier it has no accessor for; fix the ROW.)

- [ ] **Step 12: Lint**

```bash
docker compose -p ptl11 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "ruff check src tests 2>&1 | tee /app/.superpowers/run-p-tails1-t1-ruff.log"
```

Expected: `All checks passed!`

- [ ] **Step 13: Commit**

```bash
git add src/autoposter/collections/filters.py tests/test_collection_filters.py tests/test_collection_search_oracle.py tests/oracle/9b/kometa_build_filter.py
git commit --no-gpg-sign -m "feat(collections): search tails B+D -- title, edition and the five media booleans"
docker compose -p ptl11 down
```

---

### Task 2: The Person-Rows Goldens, the Rot Sweep, and the Row Closes

**Files:**
- Modify: `tests/oracle/9b/kometa_build_filter.py` (CONFIGS tail after Task 1's config 19; the `CHOICES` literal)
- Modify: `tests/test_collection_search_oracle.py` (docstring, CHOICES :110-122, CONFIGS, KOMETA)
- Modify: `src/autoposter/collections/builders/imdb_search.py` (:26)
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (:180-185, :264, :671, :266, :268, :279, :181, :182)
- Test: `tests/test_collection_search_oracle.py` (configs 20/21 are the tests)

**Interfaces:**
- Consumes: Task 1's configs 18/19 already in both CONFIGS lists (20/21 append after them; `test_the_driver_still_produces_the_pinned_strings` zips the two lists strict, so order and count must agree); the existing `actor`/`director` rows (`filters.py:1031-1066` pre-Task-1 numbering — `actor` search_field `actor`/`show.actor`, `director` movie-only).
- Produces: oracle configs `20-actor-on-a-show`, `21-people-on-a-movie`; two new CHOICES entries `("actor", "Uma Thurman"): ("6",)` and `("director", "Sofia Coppola"): ("58",)` present IDENTICALLY in the driver and the test copy (`test_the_oracles_vocabulary_fixture_matches_this_files_copy` holds them equal); the re-pointed citations; the five closed rows. Task 3 cites the closes.

- [ ] **Step 1: Append configs 20/21 and the CHOICES entries to the driver**

In `tests/oracle/9b/kometa_build_filter.py`:

(a) In the `CHOICES` literal (the driver's tag vocabulary, same shape as the test's :110-122 copy), append before the closing brace:

```python
    ("actor", "Uma Thurman"): ("6",),
    ("director", "Sofia Coppola"): ("58",),
```

(b) In `CONFIGS`, after Task 1's config 19, before the closing `]`:

```python
    # 20: ``actor`` on a SHOW library -- the phase-B person row whose
    # show_translation entry (:176) is the one thing a movie config cannot
    # reach; a wrong or absent rescope would send the bare ``actor`` field
    # and Plex would answer with the wrong set, not an error.
    ("show", {"all": {"actor": "Uma Thurman"}}),
    # 21: the people rows on a movie library -- ``actor``'s bare field beside
    # a movie-only crew row (``director``, movie_only_searches :273), both
    # resolved to keys through CHOICES like every tag. One config cannot hold
    # both halves: ``director`` on a show library is refused upstream.
    ("movie", {"all": {"actor": "Uma Thurman", "director": "Sofia Coppola"}}),
```

- [ ] **Step 2: Predict, then run the driver**

Predictions (tag values resolve to their CHOICES keys; config 17's precedent for the show tag render):

```
20 ?type=2&sort=titleSort&show.actor=6
21 ?type=1&sort=titleSort&actor=6&and=1&director=58
```

```bash
docker compose -p ptl12 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "python tests/oracle/9b/kometa_build_filter.py 2>&1 | tee /app/.superpowers/run-p-tails1-t2-driver.log"
```

Expected: twenty-one lines, 1-19 unchanged, 20/21 matching the predictions. The driver wins any disagreement.

- [ ] **Step 3: Pin configs 20/21 in the oracle test**

In `tests/test_collection_search_oracle.py`:

(a) Docstring header: `Nineteen configs` → `Twenty-one configs` (both words on :10-12).

(b) After Task 1's `## The eighteenth and nineteenth configs` section, add:

```
## The twentieth and twenty-first configs

The phase-B person rows shipped with unit tests and a live-probe verdict but
never reached a golden: the coverage test covers TYPES and ``tag`` was
already exercised, so the four rows were held only by strings derived from
the same reading of the same source -- the self-agreement this file exists to
escape, flagged by the tails recon. Config 20 pins ``actor``'s show
rescoping (``show.actor``, the divergence phase B specifically corrected
mid-task); config 21 pins its bare movie field beside a movie-only crew row
(``director``). Two configs rather than one because ``director`` on a show
library is refused by Kometa's own movie_only_searches -- there is no legal
single config holding both halves. Both goldens came from the same driver in
the same way. Unlike 18/19 these rows already existed, so the gate's red was
manufactured: ``show_search_field=None`` on the ``actor`` row turns config 20
red (``actor=6`` where Kometa says ``show.actor=6``), which is exactly the
silent-wrong-set failure the config exists to catch.
```

(c) In the test's `CHOICES` copy (:110-122), append the same two entries as the driver's, byte-identical:

```python
    ("actor", "Uma Thurman"): ("6",),
    ("director", "Sofia Coppola"): ("58",),
```

(d) `CONFIGS`, after `19-the-tails-on-a-show`:

```python
    ("20-actor-on-a-show", "show", {"all": {"actor": "Uma Thurman"}}),
    ("21-people-on-a-movie", "movie", {"all": {
        "actor": "Uma Thurman", "director": "Sofia Coppola",
    }}),
```

(e) `KOMETA`, after the 19 pin — the strings as the driver printed them:

```python
    "20-actor-on-a-show": "?type=2&sort=titleSort&show.actor=6",
    "21-people-on-a-movie": "?type=1&sort=titleSort&actor=6&and=1&director=58",
```

- [ ] **Step 4: Run — green on first run, then manufacture the red**

```bash
docker compose -p ptl12 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "timeout -s KILL 600 pytest tests/test_collection_search_oracle.py 2>&1 | tee /app/.superpowers/run-p-tails1-t2-green1.log"
```

Expected: all pass (the rows already exist — the 9b "green on first run" shape). Now prove the gate can fail. In `src/autoposter/collections/filters.py`, on the `actor` row, temporarily change `show_search_field="show.actor"` to `show_search_field=None`. Run:

```bash
docker compose -p ptl12 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "timeout -s KILL 600 pytest tests/test_collection_search_oracle.py tests/test_collection_filters.py 2>&1 | tee /app/.superpowers/run-p-tails1-t2-red1.log"
```

Expected: `test_our_url_is_byte_identical_to_kometas[20-actor-on-a-show]` FAILS — ours renders `actor=6` against the pinned `show.actor=6` (`test_every_row_searchable_on_show_carries_a_show_search_field` reds alongside it; both are the same defect seen from two gates). **Restore the row exactly**, rerun the same command, expect all green (`.superpowers/run-p-tails1-t2-green2.log`). Verify the restore with `git diff src/autoposter/collections/filters.py` → empty.

- [ ] **Step 5: Commit the goldens**

```bash
git add tests/test_collection_search_oracle.py tests/oracle/9b/kometa_build_filter.py
git commit --no-gpg-sign -m "test(collections): the phase-B person rows reach the Kometa-string oracle"
```

- [ ] **Step 6: The rot sweep — verify, then re-point, every citation**

Verification first (all were checked at plan time; re-run so the edit is against evidence, not memory):

```bash
grep -n "## 1. THE WALK LOG" .superpowers/sdd/archive/p8c-task-3-report.md          # row 148's / imdb_search.py's §1
grep -n "Kometa-complete surface remains a follow-up row" .superpowers/sdd/archive/p8c-task-3-report.md   # §6.6
grep -n "Two ceremonies registered, thirteen more" .superpowers/sdd/archive/p8c-task-4-report.md          # §6.5 (row 149)
grep -n "Golden Globes year collections include television" .superpowers/sdd/archive/p8c-task-4-report.md # §6.3 (row 150)
grep -n "One extra 398 KB fetch" .superpowers/sdd/archive/p8c-task-4-report.md                            # §6.1 (row 151)
grep -n "Where .award_filter. lives" .superpowers/sdd/archive/p4c-task-1-report.md                        # §1 (rows 149/150)
grep -n "Row 151 (fetch volume)" .superpowers/sdd/archive/p4c-task-2-report.md                            # §7.5 (row 151)
grep -n "## 4. Fixtures: the trim rule" .superpowers/sdd/archive/p4c-task-2-report.md                     # §4 (row 153)
grep -n "## 5. Tests" .superpowers/sdd/archive/p4c-task-2-report.md                                       # §5 (row 153)
test -f src/autoposter/collections/builders/imdb_lists.py && echo BUILDERS-MODULE-STILL-EXISTS
grep -n "def fetch_watchlist" src/autoposter/collections/imdb_graphql.py                                  # expect :375
```

Every grep must hit. Then the edits — all in `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` except the last:

1. Row 148 (:180): `` (`.superpowers/sdd/task-3-report.md` §1, §6.6) `` → `` (`.superpowers/sdd/archive/p8c-task-3-report.md` §1, §6.6) ``
2. Row 149 (:181), two edits: `` (`.superpowers/sdd/task-4-report.md` §6.5) `` → `` (`.superpowers/sdd/archive/p8c-task-4-report.md` §6.5) ``; and `` Detail: `.superpowers/sdd/task-1-report.md` §1, `.superpowers/sdd/task-2-report.md` | S–M `` → `` Detail: `.superpowers/sdd/archive/p4c-task-1-report.md` §1, `.superpowers/sdd/archive/p4c-task-2-report.md` | S–M ``
3. Row 150 (:182), two edits: `` (`.superpowers/sdd/task-4-report.md` §6.3) `` → `` (`.superpowers/sdd/archive/p8c-task-4-report.md` §6.3) ``; and `` Detail: `.superpowers/sdd/task-1-report.md` | S — a decision `` → `` Detail: `.superpowers/sdd/archive/p4c-task-1-report.md` | S — a decision ``
4. Row 151 (:183), two edits: `` (`.superpowers/sdd/task-4-report.md` §6.1) `` → `` (`.superpowers/sdd/archive/p8c-task-4-report.md` §6.1) ``; and `` (`.superpowers/sdd/task-2-report.md` §7.5) `` → `` (`.superpowers/sdd/archive/p4c-task-2-report.md` §7.5) ``
5. Row 153 (:185): `` Detail: `.superpowers/sdd/task-2-report.md` §4, §5 `` → `` Detail: `.superpowers/sdd/archive/p4c-task-2-report.md` §4, §5 ``
6. Row 168 (:264): `` (`collections/imdb_lists.py:366`) `` → `` (`collections/imdb_graphql.py:375` since sweep 4's rename) ``. The `builders/imdb_lists.py` mention earlier in the same row STAYS — that file exists and kept its name on purpose (row 152).
7. Prose (:671-672): replace

```
`src/autoposter/collections/imdb_lists.py` — the
   module name under-describes it, which is row 152.
```

with

```
`src/autoposter/collections/imdb_graphql.py` — renamed
   from `imdb_lists.py` by sweep 4 (row 152, CLOSED).
```

8. `src/autoposter/collections/builders/imdb_search.py:26`: `` the full probe log is in ``.superpowers/sdd/task-3-report.md`` and the fixtures `` → `` the full probe log is in ``.superpowers/sdd/archive/p8c-task-3-report.md`` and the fixtures ``

Guard grep after all eight:

```bash
sed -n '180,185p' docs/superpowers/specs/2026-08-22-full-parity-roadmap.md | grep -o "sdd/[a-z0-9/-]*task-[0-9]-report" | sort -u
grep -rn "collections/imdb_lists" docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
grep -n "task-3-report" src/autoposter/collections/builders/imdb_search.py
```

Expected: the first prints only `sdd/archive/p4c-task-...`/`sdd/archive/p8c-task-...` paths; the second matches only row 152's own historical text (:184) and row 168's `builders/imdb_lists.py`; the third shows the `archive/p8c-` path. Bare pointers OUTSIDE rows 148-153 (rows 80/81/82/93/96, prose :558/:654, filters.py:583) are OUT of scope — do not touch them.

- [ ] **Step 7: Commit the sweep**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md src/autoposter/collections/builders/imdb_search.py
git commit --no-gpg-sign -m "docs: re-point the archived report citations and the renamed transport"
```

- [ ] **Step 8: The five row closes**

All in `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`.

**Row 170 (:266).** Title: `| 170 | Search tail B: the remaining text attributes |` → `| 170 | Search tail B: the remaining text attributes (CLOSED — search-tails-1) |`. Cell: replace `the one to do first if the tail is ever picked up | S — two rows` with:

```
the one to do first if the tail is ever picked up. **closed search-tails-1:** delivered as transcribed — `title` and `edition` are `FILTER_ATTRIBUTES` rows (`src/autoposter/collections/filters.py`), each carrying the six string modifiers; `title` re-scoped to `show.title` and `edition` to `editionTitle` then `show.editionTitle` — the one row composing both translation tables — per the repo's own verbatim transcription (`tests/oracle/9b/kometa_build_filter.py:353, :86, :164, :180`). Both dual-vocabulary (`filterable=True` on `unprobed`, the `country` pattern: a `filters:` block parses the key and refuses at the accessor by naming the tier; `edition`'s movie-only filter `kinds` is stated as inferred in the row's note, the per-type filter table being untranscribed — phase B review D11, unchanged). Proven by oracle configs 18 (movie) and 19 (show), goldens produced by the pinned Kometa driver, red-first | S — two rows
```

**Row 172 (:268).** Title: `| 172 | Search tail D: the media booleans |` → `| 172 | Search tail D: the media booleans (CLOSED — search-tails-1) |`. Cell: replace `which is the same re-scoping `resolution` already does | S — five rows` with:

```
which is the same re-scoping `resolution` already does. **closed search-tails-1:** delivered as five `bool` rows, all search-only (`filterable=False`, the `decade` pattern — Kometa has no filter of any of these names, so `filters:` refuses each by pointing at the search block): `duplicate` movie-only per `movie_only_searches` (`tests/oracle/9b/kometa_build_filter.py:280`), `hdr`/`dovi`/`trash` re-scoped to the EPISODE libtype on a show library (`:182-186`) exactly as this row predicted, and `unmatched` to `show.unmatched` (`:173`) — the SHOW level, not the episode's, which is why `show_unmatched`/`episode_unmatched` stay family E's separate names (row 173). Proven by oracle configs 18/19 alongside row 170's pair | S — five rows
```

**Row 183 (:279).** Title: `| 183 | The six removed rating/plays spellings are a Kometa inheritance, not a Plex limitation |` → `| 183 | The six removed rating/plays spellings are a Kometa inheritance, not a Plex limitation (CLOSED — search-tails-1, as the documented no-op) |`. Cell: replace `so `plays.not:` would mean "everything played, except", not "everything except" | S — six operator rows, if ever asked for` with:

```
so `plays.not:` would mean "everything played, except", not "everything except". **closed search-tails-1:** as the no-op this row already documented — zero code; the refusal stands as a recorded choice, revisited only if operators ask for the spellings and then only by adding them to both vocabularies at once (row 157's lesson), and the viewCount nuance in the previous sentence is kept on the record as the reason `plays.not:` must never be added naively | S — six operator rows, if ever asked for
```

**Row 149 (:181).** Title: `| 149 | `award_filter` for multi-medium events |` → `| 149 | `award_filter` for multi-medium events (CLOSED — search-tails-1) |`. Cell: replace `Detail: `.superpowers/sdd/archive/p4c-task-1-report.md` §1, `.superpowers/sdd/archive/p4c-task-2-report.md` | S–M` (Step 6's re-pointed text) with:

```
Detail: `.superpowers/sdd/archive/p4c-task-1-report.md` §1, `.superpowers/sdd/archive/p4c-task-2-report.md`. **closed search-tails-1**, bookkeeping only: delivered by 4c/defaults-catalog exactly as recorded above; the residuals stay as notes — BAFTA configures no filter because Kometa's own bafta.yml configures none, and no ceremony's year collections filter by group | S–M
```

**Row 150 (:182).** Title: `| 150 | Award year collections resolve TV winners on Show libraries unguarded |` → `| 150 | Award year collections resolve TV winners on Show libraries unguarded (CLOSED — search-tails-1) |`. Cell: replace `Detail: `.superpowers/sdd/archive/p4c-task-1-report.md` | S — a decision` (Step 6's re-pointed text) with:

```
Detail: `.superpowers/sdd/archive/p4c-task-1-report.md`. **closed search-tails-1**, bookkeeping only: delivered by 4c/defaults-catalog exactly as recorded above — both kinds gated, the other way from Kometa, with the per-collection narrowing on top; the residual stays as a note — the Oscars remain the only ceremony on without an opt-in preset, so the gate is still not exercised by default | S — a decision
```

- [ ] **Step 9: Commit the closes**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): tails-1 closes 170/172/183 and title-closes 149/150"
docker compose -p ptl12 down
```

---

### Task 3: Wrap — Row 101's Count, the Suites, the PR Body

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (:203, row 101)
- Create: `.superpowers/sdd/p-tails1-pr-body.md` (gitignored — NEVER `git add` it)

**Interfaces:**
- Consumes: Tasks 1-2 committed (the counts below are only true with all seven rows and four configs landed).
- Produces: the final tree the user reviews before gating the PR. NO push, NO PR.

- [ ] **Step 1: Row 101's family note — the tail stands at 22**

In row 101 (:203), replace `re-derived from the library's own values rather than scored as passes | **XL` (the cell's final words) with:

```
re-derived from the library's own values rather than scored as passes. **search-tails-1 update to the family notes:** rows 170 and 172 closed — `title`, `edition` and the five media booleans (`hdr`/`dovi`/`trash`/`duplicate`/`unmatched`) ship as searches, proven by oracle configs 18-21 (which also pinned the phase-B person rows for the first time), taking the shipped searches from 26 to **33 of the 55** and the per-family tail from 29 to **22**: family E's twenty (row 173), `folder_location` (176) and `audio_codec` (177) — plus two strands that are not attributes, row 171's `current_year` value grammar half and row 179's `type:` selector | **XL
```

(The arithmetic, so a reviewer can follow: v1 shipped 19 and left 36; rows 169 (+4), 171's decade half (+1), 174 (+1) and 175 (+1) took the tail to 29; this phase's 7 take it to 22, and 20+1+1 = 22.)

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): the search tail stands at 22 -- row 101's family note"
```

- [ ] **Step 3: Full backend suite — stated, then measured**

Stated: **4190 passed, 0 failed, 0 skipped** (sweep 4's measured 4184 + 2 oracle params from T1 + 2 unit tests from T1 + 2 oracle params from T2). Then measure, with the long-run recipe (no `--rm`, detached, log read from the host):

```bash
docker compose -p ptl13 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run -d --name ptl13-full test sh -c "timeout -s KILL 2700 pytest 2>&1 | tee /app/.superpowers/run-p-tails1-full.log"
docker wait ptl13-full
docker rm ptl13-full
```

Read `.superpowers/run-p-tails1-full.log` from the host. Expected tail: `4190 passed in ...`. Any number other than 4190, or any failure: STOP, diagnose (superpowers:systematic-debugging), never rationalize the delta.

- [ ] **Step 4: Frontend suite — stated, then measured**

Stated: **358 passed / 22 files** (unchanged — this plan touches no frontend file; the run proves that claim rather than assumes it).

```bash
docker compose -p ptl13 run --name ptl13-web web npm test > .superpowers/run-p-tails1-web.log 2>&1
docker rm ptl13-web
```

Expected in the log: `Test Files  22 passed (22)` / `Tests  358 passed (358)`.

- [ ] **Step 5: Lint, whole tree**

```bash
docker compose -p ptl13 -f docker-compose.yml -f .superpowers/isolated-db.yml \
run --rm test sh -c "ruff check . 2>&1 | tee /app/.superpowers/run-p-tails1-ruff.log"
```

Expected: `All checks passed!`

- [ ] **Step 6: Write the PR body (gitignored)**

Create `.superpowers/sdd/p-tails1-pr-body.md`:

```markdown
# feat: search tails 1 — title, edition, the media booleans, and the award-family closes

## What

Seven new `plex_search` attributes, every cell transcribed from the in-repo
Kometa v2.4.8 tables (`tests/oracle/9b/kometa_build_filter.py`) and cited by
line in its row's note:

- **`title`, `edition`** (roadmap row 170) — the six string modifiers each;
  `title` → `show.title` on a show library, `edition` → `editionTitle` →
  `show.editionTitle` (the one row composing both translation tables). Both
  dual-vocabulary: `filterable=True` on the `unprobed` tier, so a `filters:`
  block parses them and refuses at the accessor by naming the tier.
- **`hdr`, `dovi`, `trash`, `duplicate`, `unmatched`** (row 172) — the bool
  renderer that shipped in 9b, five table rows, all search-only.
  `duplicate` is movie-only; `hdr`/`dovi`/`trash` re-scope to the EPISODE
  libtype on a show library, `unmatched` to `show.unmatched`.

Four new Kometa-string oracle configs (the pinned goldens produced by the
in-repo driver, red-first): 18/19 pin all thirteen field-by-libtype renders
of the seven rows; 20/21 close the flagged gap where the phase-B person rows
had never reached a golden (`show.actor` rescoping + a movie-only crew row —
two configs because `director` on a show library is refused upstream, so no
single config can hold both). Falsifiability of 20/21 proven by mutation
(`show_search_field=None` on `actor` → red → restored).

## Paperwork

- Rows 170, 172 closed (delivered here); 183 closed as its own documented
  no-op (zero code; the viewCount nuance kept); 149, 150 title-closed
  (delivered by 4c/defaults-catalog; residual notes kept).
- Row 101's family note: the search tail stands at 22 of the original 36
  (33 of 55 attributes ship).
- Rot sweep: the two stale `imdb_lists.py` citations re-pointed at
  `imdb_graphql.py` (sweep 4's rename), and the bare `task-N-report.md`
  pointers in rows 148/149/150/151/153 plus `builders/imdb_search.py:26`
  re-pointed at their archived homes (`archive/p8c-*`, `archive/p4c-*`),
  each verified against the archive's content before the edit.

## Behavior

Additive only. No engine change, no new mechanism, no migration, no config
surface change; no default definition gains anything. The golden-port
fixture is untouched. An operator who writes none of the seven new keys sees
byte-identical behavior.

## Verification

- `tests/test_collection_search_oracle.py`: 21 configs, byte-identical to
  the pinned Kometa strings; driver-vs-pins guard green.
- Full backend suite: 4190 passed (was 4184). Frontend: 358 passed. Ruff
  clean.

## Stack note

This branch is FOURTH in the stacked merge order: main ← the #103-era
history ← #108 ← #109 (`chore/sweep-4`) ← this. When #109 rebases onto a
rebased #108, this branch rebases onto the rebased #109. Merge in stack
order.
```

- [ ] **Step 7: Final checks — and stop**

```bash
git log --oneline main..HEAD    # expect 6 commits, the plan first
git status --porcelain          # expect: empty (the PR body is gitignored -- if it shows, STOP: .gitignore broke)
docker compose -p ptl13 down
```

Announce completion to the user with the branch name and the row-close list. **Do NOT push. Do NOT open a PR.** The user gates both.

---

## Self-Review (performed at plan time)

- **Spec coverage:** C1's every clause has a task — 170/172 (T1), 183/149/150 closes (T2 S8), the person-config gap (T2 S1-5), the rot sweep's three `imdb_lists.py` mentions and the report pointers (T2 S6). C2 — transcription citations in every row note (T1 S7), goldens RED via the driver (T1 S3-6), checksums/totals by measurement (T1 S9-10). C3 — closes, the 29→22 note (T3 S1), plain PR body (T3 S6). C4 — three tasks in the ordered shape, branch/stack/artifacts in the Global Constraints.
- **Placeholder scan:** none — every code step carries the code, every command its expected output.
- **Type consistency:** the seven row names and their order (`title`, `edition`, `hdr`, `dovi`, `trash`, `duplicate`, `unmatched`) are identical in T1 S7 (the table), T1 S10 (the pinned lists), and T3 S1 (the note); config ids `18-the-tails-on-a-movie`/`19-the-tails-on-a-show`/`20-actor-on-a-show`/`21-people-on-a-movie` are identical across driver comments, CONFIGS, and KOMETA; the CHOICES entries are byte-identical in both copies (the equality test enforces it).
- **Arithmetic:** 26+7=33 rows; str 1+2=3, bool 2+5=7; unprobed 5+2=7, search-only 3+5=8; kinds 15+5=20 both / 10+2=12 movie / 1 show; search_kinds 18+6=24 / 7+1=8 / 1; searchable 26+7=33, filterable 23+2=25; tail 36−4(169)−1(171's decade half)−1(174)−1(175) = 29 before this phase, −7 = 22 = 20(E)+1(176)+1(177). Tests 4184+6=4190.
