# Phase 10c-lite: People (the shippable half) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lift the first of the five deferred items off `builders/tmdb_person.py`'s
8c hard stop — a person's TMDb biography becomes their filmography collection's
summary and their TMDb profile photo becomes its poster, on all five shipped
person builders — decide `tmdb_popular_people` on an upstream read rather than a
guess, and file the expensive half (the appearance-threshold dynamic person
types) as a roadmap row with the same argument row 192 got, so row 83 can close.

**Architecture:** One new TMDb transport method (`/person/{id}`, beside the
`person_credits` endpoints already there), one contained read in the shared
`_TmdbPersonBuilder.build` that fills `BuilderResult.summary`/`poster_kind`/
`poster_key`, and one new poster SOURCE function in `collections/posters.py`
feeding the generic `apply_poster` machinery that already fetches, validates,
hashes and uploads. No new config field, no new endpoint, no new dynamic type,
no credit scan, no engine change.

**Tech Stack:** Python 3.14, pydantic v2, httpx (`MockTransport` in tests),
pytest, ruff (line-length 100), Docker Compose. **No new runtime dependency.**

---

## Read this first — what governs this plan

1. `.superpowers/sdd/p10c-facts.md` **C1–C8** — the controller adjudications.
   They are **SETTLED**. This plan implements them; where the evidence
   disagrees with itself, the CONTRADICTION FLAGS section below says so and the
   adjudicated version is what gets built anyway.
2. The 10c recon's key facts (the recon itself is in the session record). Every
   one of its citations used below was **re-read at `origin/main` while this
   plan was written** and is listed with its line numbers in "The tree, as read"
   two sections down.
3. The tree itself.

---

## What this phase is NOT

- **No dynamic person types.** No `actor`/`director`/`writer`/`producer`
  dynamic type, no `depth`/`limit` appearance threshold, no row in
  `dynamic_types.py`'s `DYNAMIC_TYPES`. That is C1's FILE half and becomes
  roadmap row 194.
- **No library-wide credit scan.** Nothing in this phase reads people off a
  Plex ITEM. Every person read here is one person, by TMDb id, in at most two
  cached requests.
- **No row 169 work.** `actor`/`director`/`writer`/`producer` do not join
  `FILTER_ATTRIBUTES`. They are absent today (`filters.py:890`'s `BY_NAME` is
  built from `FILTER_ATTRIBUTES` and holds no such row), which is exactly why
  the dynamic types cannot be written as smart collections yet.
- **No birthday/deathday gating** (C4). It is filed, not built — see Task 4.
  The `/person/{id}` payload carries `birthday` and `deathday`; this phase
  deliberately does not carry them out of the transport.
- **No new config knob.** The bio/poster read is not opt-in, not opt-out and
  has no setting. `collections.posters` (`posters.posters_enabled`) already
  gates every poster in this service and gates this one unchanged; a
  definition's own `summary:` already wins over a builder's
  (`engine._summary_for`), and a local poster file already wins over any
  hosted source (`posters.apply_poster`).
- **No change to `hosted_poster_url`'s six-kind table** (C2). It keeps its six
  kinds and its `Default-Images` base.
- **No `catalog.py` split.** Fourth triage: this phase edits four descriptions
  and one constant. Nothing structural.

---

## CONTRADICTION FLAGS

Three places where the evidence disagrees with itself or with the tree. Each is
flagged here and **planned as the controller adjudicated it**.

**FLAG 1 — C2 says both fence tests go red; only one of them can.** C2 names
`test_a_credit_carries_only_what_the_role_table_reads`
(`tests/test_builder_tmdb_person.py:361`) and
`test_a_person_builder_offers_no_summary_and_no_poster` (`:421`) as "the
intended break-sites: they go red deliberately". The second genuinely does —
it asserts `result.summary is None` and `(poster_kind, poster_key) == (None,
None)`, which is precisely what this phase stops being true. The first does
**not**, and cannot, under C2's own adjudicated design: it asserts
`set(PersonCredit.__dataclass_fields__) == {"tmdb_id", "kind", "job",
"department"}`, and C2 puts the biography and the profile path on a **new
`/person/{id}` detail call**, not on the credit entries. `PersonCredit` keeps
its four fields, so the assertion stays true and the test stays green.
**Planned:** the design is the substantive adjudication and it is built as
written. Test `:361`'s **assertion is kept unchanged** — "a credit carries only
what the role table reads" is still a true and load-bearing invariant, and it
is worth MORE after this phase, not less: it is now what stops a later hand
from reading a profile path off a credit entry instead of off the person's own
record. Its **docstring is rewritten** in the same commit, because "The 10c
line" and "biographies one attribute access away from a builder that must not
grow them in this phase" become false the moment this phase ships. The red-first
discipline is satisfied by test `:421` (a real red) plus every new test in Task
2, each of which is written and run red before its implementation. Task 2 Step
11 additionally proves `:361` still has teeth with a backup+cmp mutation.

**FLAG 2 — `origin/main` has moved past the sha in the brief.** The brief says
"the tree at origin/main (03c6458)". At the time this plan was written
`origin/main` is **`fcf871b`**; `03c6458` is four commits back. The four
commits between them are `1509f16` (node toolchain), `995c533`
(`@testing-library/react`), `82bfcae` (`@vitejs/plugin-react`) and `fcf871b`
(`react-router-dom`) — `git diff --stat 03c6458 fcf871b` touches exactly
`.forgejo/workflows/ci.yml`, `Dockerfile`, `frontend/package.json` and
`frontend/package-lock.json`, and **no Python file at all**. Every `src/` and
`tests/` citation in this plan therefore holds at both. **Planned:** cut from
`origin/main` (whatever its sha is at cut time) and verify by CONTENT, per the
global constraint — never by sha. Record the resolved sha in the Task 1 report.

**FLAG 3 — C7's "cuts from origin/main AFTER the deferred-jobs PR merges" is
already satisfied, and the branch the brief's git status shows is not it.**
`git branch -r --merged origin/main` lists `origin/feat/deferred-jobs`, so that
work is in. **Planned:** no stacking, no waiting; the content probe in "Branch
and cut point" checks for a deferred-jobs artifact so a stale `origin/main`
cannot be cut from silently.

---

## The tree, as read

Every line number below was read at `origin/main` while this plan was written.

**The 8c hard stop and the builders**
- `src/autoposter/collections/builders/tmdb_person.py:64-83` — the "Deliberately
  absent -- this is the 10c line, and it is a hard stop" block, five deferred
  items. `:69-70` is the item this phase lifts ("Both are left `None`, exactly
  as the TMDb chart builders leave them").
- `:159-201` — `_TmdbPersonBuilder`, `params_model = TmdbEntityParams`,
  `media_types = {"Movie": "movie", "Show": "tv"}`, and `build`, which ends
  `return BuilderResult(ids=[("tmdb", value) for value in ids])`.
- `:204-232` — the five subclasses, one `type_name` each.
- `src/autoposter/collections/builders/tmdb.py:143-157` — `TmdbEntityParams`
  (`extra="forbid"`, `id: int = Field(gt=0)`); `:160-170` — `_TmdbBuilder._client`,
  which raises `TmdbBuilderRefused` when `ctx.sources.tmdb` is None.
- `src/autoposter/collections/builders/__init__.py:112-118` — the five
  `register(...)` calls under their comment.

**The transport**
- `src/autoposter/providers/tmdb_lists.py:104-130` — `PersonCredit`, four
  fields, with the docstring paragraph that names bios/profile paths as 10c's.
- `:132-141` — `TmdbListRefused`, one class for the three cases.
- `:166-187` — `_get(path, params, subject)`: one request through
  `fetch_json`, 404 → `TmdbListRefused`. `:189-250` — `_paged`.
- `:307-356` — `person_credits(person_id, media_type)`, the only person method
  in the tree. No `/person/{id}` detail call, no `/person/popular`.
- `:94-101` — `__all__`.
- `src/autoposter/providers/tmdb.py:9-10` — `BASE_URL` and
  `IMAGE_BASE = "https://image.tmdb.org/t/p/original"`. No person endpoints in
  this module.

**The result contract and where it goes**
- `src/autoposter/collections/builders/base.py:88-109` — `BuilderResult(ids,
  summary=None, poster_kind=None, poster_key=None)` and the docstring that says
  artwork "belong[s] to the builder because the hosted default is keyed by what
  the *source* calls the collection".
- `src/autoposter/collections/engine.py:509` — `_summary_for(definition,
  result, summaries)`; `:625-664` — its three sources in order, the config's
  first, and `return definition.summary or result.summary, None` at `:644`.
- `src/autoposter/collections/engine.py:539-555` — `reconcile_list_collection`
  called with `kind=result.poster_kind, key=result.poster_key`.
- `src/autoposter/collections/lists.py:375-380` — the `apply_poster` call.

**The poster machinery**
- `src/autoposter/collections/posters.py:1-18` — module docstring ("Two pure
  decisions").
- `:97-129` — `hosted_poster_url(kind, key)`, six kinds
  (`award_static`, `award_year`, `chart`, `content_rating`,
  `content_rating_other`, `separator`), all `DEFAULT_IMAGES_BASE` paths.
- `:207-214` — `posters_enabled`; `:217-230` — `_is_image`; `:258-275` —
  `fetch_poster`; `:278-363` — `apply_poster`, with the hosted branch at
  `:339-346` (`url = hosted_poster_url(kind, key)`; `None` → "no poster source
  for %r").
- `src/autoposter/collections/builders/imdb_award.py:720-731` — the worked
  precedent for a builder returning summary + poster together.

**The tests**
- `tests/test_builder_tmdb_person.py:1-33` — module docstring, including the
  fixtures paragraph ("TMDb's response *shape*, not recordings of a real
  person").
- `:53-54` — `MOVIE_CREDITS`/`TV_CREDITS`; `:61-64` — `_path`; `:67-76` —
  `_routed(routes, seen)`; `:79-88` — `_credits`/`_both`; `:91-109` —
  `_sources`/`_ctx`/`_build`/`_ids`.
- `:361-366` — fence test A (`PersonCredit` field set). **FLAG 1.**
- `:417-431` — the "the 10c boundary" section and fence test B.
- `tests/test_collection_poster_apply.py:17` — imports `_write_and_upload` and
  `apply_poster`; the file is the poster suite this phase extends.
- `tests/test_builder_port_golden.py:1-42,57` — the golden gate and its fixture
  path `tests/fixtures/collections/golden_port.json`.

**The catalog and the paperwork**
- `src/autoposter/collections/catalog.py:661` —
  `PERSON_DYNAMIC_ROW = 83    # phase 10c: the dynamic half of the person builders`.
  Read at exactly one place: `:1733` (`gated_row=PERSON_DYNAMIC_ROW`).
- `:1660-1692` — the people-packs comment, `_STARTER_DIRECTORS` (six ids) and
  `_PERSON_PACKS` (the four gated rows).
- `:1694-1736` — `PEOPLE_PRESETS`: `people_directors` (READY) at `:1695-1718`
  with the description at `:1699-1707`, and the four gated rows at `:1719-1736`
  with the shared description at `:1724-1729` — the "with a poster and biography
  from TMDb" sentence is `:1725-1726`.
- `src/autoposter/collections/dynamic_types.py:38-39` — "the people types
  (10c/row 83), ``tmdb_collection``, ``trakt_*``, ``number``, ``custom`` and the
  music types -- none of them is a library enumeration." One clause, no
  argument, while every neighbour at `:30-37` gets one.
- `tests/test_collection_catalog.py:57-60` — `ROADMAP` path; `:79-91` —
  `_roadmap_rows()`, which regex-reads `^|\s*(\d+)\s*|` out of the roadmap;
  `:265-275` — `CATALOG_CHECKSUM`, `"people": (1, 4, 0)`; `:278-295` — the
  checksum test; `:1054-1067` — `test_every_gated_row_cites_a_roadmap_row_that_exists`;
  `:1070-1085` — `test_no_preset_still_waits_on_the_row_the_dynamic_engine_closed`.
- `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:179` — row 83.
  Its `Detail:` reads `.superpowers/sdd/task-2-report.md`, which today is
  **10b's** Task 2 report (6.4K); 8c's was archived to
  `.superpowers/sdd/archive/p8c-task-2-report.md` (23.8K, confirmed present).
- `:288` — row 192, the model this phase's new row is graded against.
- `:289` — row 193, **the last filed row. The new row is 194.** (Verified by
  reading the table, not by recall.)
- `:256` — row 160, "Day-level seasonal windows, and a collection fed by
  several builders".
- `:265` — row 169, "Search tail A: the people tags" — `actor`, `director`,
  `producer`, `writer`, needing Kometa's `get_actor_id`/`hubSearch` lookup
  mechanism, not just table rows.
- `:902-903` — "the four people packs among the eighteen are row 83's and phase
  10c's rather than this row's". **C6(d), found while planning** — see Task 4.
- `:943-955` — the `#### 10c — People` section, whose **Closes:** line names
  "the person dynamic types of row 102", a CLOSED row.

---

## Branch and cut point

- Branch name: **`feat/people-lite`**.
- **Cut from `origin/main` after a fetch, verified with a CONTENT probe, never
  a sha probe:**

  ```bash
  git fetch origin
  git show origin/main:src/autoposter/providers/tmdb_lists.py | grep -q 'async def person_credits' && echo CREDITS-PRESENT
  git show origin/main:src/autoposter/providers/tmdb_lists.py | grep -q '/person/{person_id}' && echo NO-DETAIL-CHECK-MANUAL
  git show origin/main:src/autoposter/collections/builders/tmdb_person.py | grep -q 'this is the 10c line' && echo HARDSTOP-PRESENT
  git show origin/main:src/autoposter/collections/posters.py | grep -q 'async def apply_poster' && echo APPLY-PRESENT
  git show origin/main:src/autoposter/collections/catalog.py | grep -q 'PERSON_DYNAMIC_ROW = 83' && echo ROW83-PRESENT
  git show origin/main:src/autoposter/db/models.py | grep -q 'deferred' ; echo "deferred-jobs probe exit=$?"
  git branch -r --merged origin/main | grep -q 'origin/feat/deferred-jobs' && echo DEFERRED-JOBS-MERGED
  git checkout -b feat/people-lite origin/main
  git rev-parse HEAD
  ```

  `CREDITS-PRESENT`, `HARDSTOP-PRESENT`, `APPLY-PRESENT`, `ROW83-PRESENT` and
  `DEFERRED-JOBS-MERGED` must all print. If `DEFERRED-JOBS-MERGED` does not,
  `origin/main` predates the deferred-jobs merge — **STOP and report** (C7: this
  branch does not stack on a phase branch). Record the resolved
  `git rev-parse HEAD` in the Task 1 report; at writing time `origin/main` is
  `fcf871b` (FLAG 2).

  Also confirm the negative the whole phase rests on:

  ```bash
  git show origin/main:src/autoposter/providers/tmdb_lists.py | grep -c 'person'
  ```

  Every hit must be inside `person_credits`/`PersonCredit`. A `/person/{id}`
  detail call or a `/person/popular` call already present means the recon is
  stale — **STOP and report**.

## Execution

Executed via **superpowers:subagent-driven-development**: one fresh subagent per
task, two-stage review between tasks, this plan **live-synced** — a fix that
changes a signature, a message or a table edits the corresponding step here in
the same commit, so a later task's implementer reads the truth and not the
original guess.

---

## Global Constraints

Every task's requirements implicitly include this section. The clauses in
**bold** are carried verbatim from the phase brief.

1. **"Transcriptions fetched-not-recalled."** Every statement this phase makes
   about Kometa comes from a file fetched at tag `v2.4.8` and recorded in
   `.superpowers/sdd/p10c-upstream-person.md` with its path, byte count and
   fetch date. A recalled upstream behaviour is a fabricated attribution and is
   the one failure this discipline exists to prevent. If a fetch 404s, say so
   in the record and mark the dependent decision NOT_KOMETA — never substitute
   a neighbouring file.
2. **"NOT_KOMETA for anything invented."** Anything this service decides for
   itself carries the `NOT_KOMETA` prefix (`catalog.py:534`, the string
   `"no Kometa defaults file -- "`) in a catalog row's `kometa_source`, or an
   explicit `# NOT KOMETA:` comment naming who decided it and why, everywhere
   else. A preset's `kometa_source` that is a path must stay a path
   `_KOMETA_DEFAULTS` pins.
3. **"Refusals RETURN."** No new raise on a cosmetic path. Membership failure
   raises (a builder that returns `[]` is read one layer down as "make no
   changes" — `lists.reconcile_list_collection`); a biography, a poster or a
   person's own record failing to load is CONTAINED, logged, and leaves the
   collection reconciled. Nothing in this phase converts an existing returned
   refusal into a raise.
4. **"No URLs/tokens in tracked files (TMDb API paths/ids fine)."** No operator
   URL, no host, no bearer token in code, comments, tests, fixtures, logs,
   reports or the PR body. TMDb API *paths* (`/person/{id}`) and TMDb ids are
   fine and are the point. The two constants that ARE URLs
   (`DEFAULT_IMAGES_BASE`, `IMAGE_BASE`) already exist and are not new. Upstream
   GitHub URLs at a pinned tag belong in `.superpowers/` (gitignored) records
   only.
5. **"Golden gate byte-identical."** `tests/fixtures/collections/golden_port.json`
   must not change in any task. No golden scenario builds a person collection,
   so this phase is invisible to it — **assert that with a real run at the end
   of every task that touches `src/`**, never by assuming it.
6. **"Container discipline."** Unique compose project per task (`p10ct1` …
   `p10ct4`), always with the `.superpowers/isolated-db.yml` overlay. Tee output
   to a path under `/app/.superpowers/` inside the container — never rely on
   streamed stdout (it is filtered, and `--rm` deletes the container before it
   can be re-read). A long run (the full suite) uses **no `--rm`**, is started
   detached with `run -d --name <project>-full`, and is waited on with a
   foreground `docker wait`; the log is then read from the host. Teardown is
   `docker compose -p <project> down` — **never** `down -v`.
7. **"Mutation proofs backup+cmp."** Copy the file, edit the copy's source in
   place, run the test to see it RED, restore from the backup, `cmp` the
   restored file against the backup to prove the restore was exact, re-run to
   see GREEN. Paste real output, both halves. A mutation proof with no pasted
   RED half is not a proof.
8. **"Conventional commits `--no-gpg-sign`, no attribution."** Staged **by
   name** (never `git add -A`). No `Co-Authored-By`, no AI attribution of any
   kind, in commits or in the PR body.
9. **"Branch cut from `origin/main` after fetch with a content probe."** See
   "Branch and cut point". Never a sha probe (FLAG 2).
10. **No count-bearing docstrings.** A docstring that states a number some other
    file owns goes stale silently. Say "the five person builders" only where
    `ROLES` is the thing being described; never write a count of roadmap rows,
    presets or fixtures into prose that nothing checks.
11. **Every refusal names the way out.** A message that says only what is wrong
    is half a message.

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `.superpowers/sdd/p10c-upstream-person.md` | **Create.** The fetch record: what upstream does with the person detail payload, whether the people packs set per-key images, whether `Default-Images` has a people folder, and the decisive `tmdb_popular_people` shape answer. Fixed section numbers §0–§5, cited by Tasks 2, 3 and 4 | T1 |
| `src/autoposter/providers/tmdb_lists.py` | **Modify.** `PersonDetail` (two fields) + `person_detail(person_id)`; `__all__`; T3-branch-A only: `PopularPerson` + `popular_people(limit)` | T2, T3 |
| `src/autoposter/collections/posters.py` | **Modify.** `TMDB_PROFILE_KIND`, `tmdb_profile_url(profile_path)`, the one-line source dispatch in `apply_poster`, and the module docstring's "two pure decisions" line | T2 |
| `src/autoposter/collections/builders/tmdb_person.py` | **Modify.** The contained `_profile` read and the widened `BuilderResult`; the hard-stop docstring loses its first item and gains the lifted-item note | T2 |
| `src/autoposter/collections/builders/tmdb_popular_people.py` | **Create — branch A only.** The popular-people builder | T3 |
| `src/autoposter/collections/builders/__init__.py` | **Modify — branch A only.** One import, one `register(...)` | T3 |
| `tests/fixtures/collections/tmdb_person_detail.json` | **Create.** The `/person/{id}` response SHAPE, constructed, carrying the fields that must not cross as well as the two that must | T2 |
| `tests/fixtures/collections/tmdb_popular_people_p1.json` | **Create — branch A only.** | T3 |
| `tests/test_builder_tmdb_person.py` | **Modify.** Fence test B replaced by the behaviour tests; fence test A's docstring rewritten (FLAG 1); the two route helpers gain the detail path; the module docstring gains the new fixture's provenance paragraph | T2 |
| `tests/test_collection_poster_apply.py` | **Modify.** The `tmdb_profile` source: URL shape, unusable path, local override still wins, dry-run unchanged | T2 |
| `tests/test_builder_tmdb_popular_people.py` | **Create — branch A only.** | T3 |
| `src/autoposter/collections/catalog.py` | **Modify.** `PERSON_DYNAMIC_ROW` → `PERSON_SCAN_ROW = 194`; the four gated descriptions (C5); `people_directors`' description gains the bio/poster sentence | T4 |
| `src/autoposter/collections/dynamic_types.py` | **Modify.** The one-clause people absence (`:38-39`) gains its per-type argument (C6c) | T4 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modify.** New row 194; row 83 closed + `Detail:` re-pointed (C6a); row 160 gains the birthday sentence (C4); the 10c section re-scoped and its **Closes:** re-pointed (C6b); line 902's carve-out re-pointed (C6d) | T4 |
| `tests/test_collection_catalog.py` | **Modify.** One line of `test_every_gated_row_cites_a_roadmap_row_that_exists`' parse assertion, plus the new-row guard | T4 |
| `.superpowers/sdd/p10c-pr-body.md` | **Create.** The PR body | T4 |

---

## Task 1: The upstream read

**Files:**
- Create: `.superpowers/sdd/p10c-upstream-person.md`
- Read only: `src/autoposter/collections/builders/tmdb_person.py`,
  `src/autoposter/providers/tmdb_lists.py`,
  `src/autoposter/collections/posters.py`,
  `src/autoposter/collections/catalog.py:1660-1736`

**Interfaces:**
- Consumes: nothing. **No code changes in this task.**
- Produces: the record every later task cites, with fixed section numbers:
  **§0** the fetch log; **§1** what `tmdb_person` does with the detail payload;
  **§2** the people packs' per-key images and the `Default-Images` people
  question; **§3** `tmdb_popular_people`'s shape and **the T3 decision**;
  **§4** the birthday/deathday mechanism (one paragraph, for C4's filing);
  **§5** the NOT-KOMETA list — every question the fetch could NOT answer.

**Background.** No transcription of upstream's person surface exists anywhere in
this repository: `docs/research/kometa-collections.md` (35.8K, the awards and
charts record) contains no occurrence of `tmdb_person`, `popular_people`,
`birthday`, `profile_path` or `biography`, and neither does
`.superpowers/sdd/p10a-upstream-dynamic.md` (which transcribed the dynamic
ENGINE). This task is the whole reason the phase has four tasks and not three:
C2 calls the person-poster mechanism "UNRESEARCHED (recon §2.4 — firm
negative)" and C3 makes the entire existence of Task 3 conditional on §3's
answer.

The model for this record's rigour is `.superpowers/sdd/p10a-upstream-dynamic.md`
— pinned tag, blob-URL form stated once at the top, every claim carrying a
`file:line`.

- [ ] **Step 1: Cut the branch**

Run the whole "Branch and cut point" block above. Paste every probe's output and
`git rev-parse HEAD` into the task report. If any required probe fails, STOP.

- [ ] **Step 2: Load `WebFetch`**

`WebFetch` is a deferred tool. Load it before use:

```
ToolSearch with query "select:WebFetch"
```

- [ ] **Step 3: Fetch the upstream files, at the pinned tag, raw**

Kometa **v2.4.8** — the same tag every prior phase transcribed from. Raw form:
`https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/<path>`.

Fetch these, in this order:

| # | path | what it answers |
| --- | --- | --- |
| 1 | `modules/builder.py` | the `tmdb_person` builder itself: which TMDb call it makes, and what it does with `biography` and `profile_path` |
| 2 | `modules/meta.py` | the dynamic-collection template at/around lines 36–50 (C3 cites `meta.py:42-43`), and the `tmdb_popular_people` type's own branch |
| 3 | `modules/tmdb.py` | the TMDb wrapper: the person-detail call, the image-URL construction and the size segment it uses |
| 4 | `defaults/both/actor.yml` | whether the people packs set per-key images; the pack's `template_variables` |
| 5 | `defaults/movie/director.yml` | the same, for the pack `people_top_directors` cites |
| 6 | `defaults/movie/writer.yml` | the same |
| 7 | `defaults/movie/producer.yml` | the same |

`modules/builder.py` and `modules/meta.py` are large. Fetch them whole; do not
fetch a line range and then reason about what was outside it.

Then answer the `Default-Images` question by fetching the repository listing
rather than guessing a path:
`https://api.github.com/repos/Kometa-Team/Default-Images/contents/` and, if a
people-ish folder exists, its listing too. **A 404 is an answer** — record it as
"no people folder at `master`, checked <date>" and move on. Do not construct a
`Default-Images` people URL from a pattern; `posters.py:65-85`'s `AWARD_SEGMENTS`
comment exists precisely because a derived folder name was wrong in both
directions.

If any fetch 404s, record the 404 verbatim in §0 with the URL attempted, try the
same path at the tag's own tree listing, and if it is genuinely absent say so in
§5 rather than substituting another file.

- [ ] **Step 4: Write §0 — the fetch log**

One row per fetch: URL, date, byte count, and the first non-comment line. A
later reader must be able to tell whether they are looking at the same file.
Include the 404s.

- [ ] **Step 5: Write §1 — what `tmdb_person` does with the detail payload**

Answer each of these with a `file:line` citation, and write "(not found —
searched `<terms>` in `<files>`)" where the answer is absent rather than leaving
a gap:

1. Which endpoint does upstream call for a person's own record, and with which
   parameters (`language`? `append_to_response`?)
2. **Biography → summary.** Is the biography used verbatim, truncated, prefixed,
   or wrapped in a translation string? If it is empty, what does upstream set?
   Is there a language fallback (TMDb returns `""` for a person with no bio in
   the requested language)?
3. **`profile_path` → poster.** What URL does upstream build from it — which
   base, and **which size segment** (`original`, `w500`, …)? This is the one
   value Task 2 hard-codes by reference: `providers/tmdb.py:10` already pins
   `IMAGE_BASE = "https://image.tmdb.org/t/p/original"`, so record whether
   upstream agrees, and if it does not, record what it uses and say so as a
   divergence rather than changing `IMAGE_BASE` (that constant is the artwork
   pipeline's and is out of this phase's scope).
4. Does upstream set the poster as a URL for Plex to fetch, or does it download
   the bytes itself? (Ours downloads: `posters.apply_poster` never uses
   plexapi's `url=` form, deliberately — `posters.py:302-306`.)
5. Is `tmdb_person` a *builder* (produces membership) or a *detail setting*
   (produces summary/poster for a collection whose membership comes from
   elsewhere)? Quote the code that settles it.

- [ ] **Step 6: Write §2 — per-key images and `Default-Images`**

Two questions, one paragraph each:

1. Do `actor.yml` / `director.yml` / `writer.yml` / `producer.yml` set a
   per-key `image` (the way the award files do, which is where
   `posters.AWARD_SEGMENTS` came from)? Transcribe the whole
   `template_variables` block of each if so.
2. Does `Kometa-Team/Default-Images` have a people folder at all? Name the
   folder or record the 404.

Close with the consequence in one sentence: if there is neither a per-key image
nor a people folder, then the TMDb profile photo is the ONLY poster source a
person collection can have, which is what makes Task 2's new source function
load-bearing rather than an extra.

- [ ] **Step 7: Write §3 — `tmdb_popular_people`'s shape, and the T3 decision**

This is the section Task 3 is gated on. Answer, with citations:

1. Is `tmdb_popular_people` a *dynamic collection type* (`meta.py`), a
   *builder* (`builder.py`), or both?
2. What does one of its collections CONTAIN? Quote the template. C3's evidence
   says `meta.py:42-43` is `tmdb_person` + an actor-tag search — confirm or
   refute it against the fetched file.
3. **The decisive question:** does the shape upstream ships require the Plex
   **actor tag** — a `plex_search`/`plex_all` on `actor:` — to express?

Then write the verdict as one of exactly these three lines, verbatim, so Task 3
can dispatch on it without interpretation:

- `VERDICT A-EXPAND: upstream expands to one per-person collection and does not
  need the Plex actor tag.`
- `VERDICT A-SINGLE: upstream builds ONE collection whose members are titles
  (e.g. the popular people's known-for titles) and does not need the Plex actor
  tag.`
- `VERDICT B-GATED: upstream's shape requires the Plex actor tag, which is
  roadmap row 169's machinery, so popular-people is gated too and is FILED with
  the new row rather than shipped.`

If the fetch cannot settle it, the verdict is **B-GATED**. That is not a
tie-break for convenience: shipping a shape we cannot show is upstream's, under
upstream's name, is exactly the fabricated attribution constraint 2 forbids, and
C3's own words are "Honest outcome over forced shipping."

Under A-EXPAND or A-SINGLE, also record: the collection **title format** (with
its exact token spelling), the **default count** (how many people), whether
`limit`/`depth` are configurable and their defaults, the **library types** the
pack allows, and the summary each collection carries. Anything the file does not
carry goes in §5 as NOT KOMETA, and Task 3 must state it in the row.

- [ ] **Step 8: Write §4 — birthday/deathday, for C4's filing**

One paragraph, with citations: what does Kometa gate on a birthday — the
COLLECTION's existence, or the RUN? Which config key, on which object, and what
is the window (a day? a range)? This paragraph is the source for one sentence in
Task 4's roadmap edit and for nothing else; do not build anything from it.

Also record the one tree-side fact that makes it cheap later: `/person/{id}`
carries `birthday` and `deathday`, and Task 2 deliberately does not carry them
out of the transport.

- [ ] **Step 9: Write §5 — the NOT-KOMETA list**

Every question above that the fetch could not answer, one line each, in the form
"<question> — not answered by <files fetched>; whatever this service does here
is OURS." This is the list Task 2, 3 and 4 quote from when they write a
`# NOT KOMETA:` comment or a `NOT_KOMETA` row.

- [ ] **Step 10: Commit the record**

```bash
git add .superpowers/sdd/p10c-upstream-person.md
git commit --no-gpg-sign -m "docs(10c): the upstream person read, fetched at v2.4.8"
```

`.superpowers/` is gitignored (`.gitignore:23`), so this commit will stage
nothing. **That is expected** — run it, observe "nothing to commit", and record
that in the report. The record's durability is the file on disk plus the report,
exactly as `p10a-upstream-dynamic.md`'s is. Do not `git add -f`.

- [ ] **Step 11: Report the verdict**

The task report's first line is the §3 verdict, copied verbatim. Task 3's
implementer reads that line and nothing else to know which branch to build.

---

## Task 2: The person's own record — biography as summary, profile photo as poster

**Files:**
- Modify: `src/autoposter/providers/tmdb_lists.py` (`PersonDetail` +
  `person_detail`, and `__all__` at `:94-101`)
- Modify: `src/autoposter/collections/posters.py` (`TMDB_PROFILE_KIND`,
  `tmdb_profile_url`, the dispatch line inside `apply_poster` at `:340`, and the
  module docstring at `:1-6`)
- Modify: `src/autoposter/collections/builders/tmdb_person.py` (docstring
  `:64-83`, `build` `:175-201`, a new `_profile` method, the import block)
- Create: `tests/fixtures/collections/tmdb_person_detail.json`
- Modify: `tests/test_builder_tmdb_person.py` (docstring, `:53-54`, `:79-88`,
  `:361-366`, `:417-431`, plus new tests)
- Modify: `tests/test_collection_poster_apply.py`

**Interfaces:**
- Consumes: `.superpowers/sdd/p10c-upstream-person.md` §1 (the biography and
  size-segment answers) and §5 (what is ours). `TmdbListClient._get(path,
  params, subject) -> dict` (`tmdb_lists.py:166`), which raises
  `TmdbListRefused` on 404. `IMAGE_BASE` (`providers/tmdb.py:10`).
  `BuilderResult(ids, summary, poster_kind, poster_key)`
  (`builders/base.py:88-109`).
- Produces, and Tasks 3 and 4 rely on these exact names:
  - `providers.tmdb_lists.PersonDetail` — frozen dataclass,
    `biography: str | None`, `profile_path: str | None`.
  - `async TmdbListClient.person_detail(person_id: int) -> PersonDetail`.
  - `collections.posters.TMDB_PROFILE_KIND: str` — the literal `"tmdb_profile"`.
  - `collections.posters.tmdb_profile_url(profile_path: str) -> str | None`.
  - `_TmdbPersonBuilder` now returns `BuilderResult` with `summary` and
    `poster_kind=TMDB_PROFILE_KIND`/`poster_key=<profile path>` filled when the
    person's record carries them.

**The design, and the two things it deliberately does not do.**

*One extra request per person definition, and it is cached.* `person_detail`
goes through `_get`, so it is one `fetch_json` through the `ProviderCache` at the
client's TTL, keyed on method+URL+params (never the bearer token —
`tmdb_lists.py:169-171`). A person's biography changes about never; their
filmography changes more often. Two endpoints, two cache keys, is the right
shape.

*The detail read is CONTAINED; the credits read is not.* This asymmetry is the
whole design and every reviewer will ask about it. The credits ARE the
membership: a failure there must raise, because an empty list one layer down
means "remove every member" (`lists.reconcile_list_collection`, quoted in
`builders/base.py:11-14`). A biography and a poster are cosmetic — `posters.py`
says so out loud for every other source at `:315-317` ("a missing poster is
cosmetic and must never fail the surrounding pass") — so a second endpoint being
down must leave the membership reconciled and the artwork untouched. It is
called **after** the credits, so a wrong id fails once on the membership call
rather than making a second doomed request.

*TMDb's profile path is not a row of `hosted_poster_url`'s table* (C2). Every
kind there is a `Default-Images` path built from a key THIS SERVICE chose — a
chart name, a ceremony year, a content-rating bucket. A profile path is an
opaque file path TMDb handed back. They share nothing but returning a string, and
putting one in the other's table would put a provider's opaque path in a column
of curated folder names. So: a separate source function, and a one-line dispatch
in `apply_poster` — which is the only place that has ever needed to turn a
(kind, key) pair into a URL.

- [ ] **Step 1: Write the new fixture**

Create `tests/fixtures/collections/tmdb_person_detail.json`. It is TMDb's
response SHAPE, constructed — the same rule the credits fixtures follow
(`tests/test_builder_tmdb_person.py:26-32`). It carries the two fields that must
cross AND the fields that must not, so "nothing else crosses" is exercised
rather than asserted:

```json
{
  "id": 4242,
  "name": "A Constructed Person",
  "biography": "A constructed biography. It exists to be read as a collection summary, and it says so out loud, so that nothing in this fixture can be mistaken for a transcription of a real person's TMDb record.",
  "profile_path": "/a-constructed-profile-path.jpg",
  "birthday": "1970-01-01",
  "deathday": null,
  "place_of_birth": "Nowhere In Particular",
  "also_known_as": ["Another Constructed Name"],
  "known_for_department": "Directing",
  "popularity": 12.5,
  "adult": false,
  "gender": 0,
  "homepage": null,
  "imdb_id": null
}
```

`"id": 4242` matches the credits fixtures' person so one route table serves both.

- [ ] **Step 2: Write the failing transport tests**

In `tests/test_builder_tmdb_person.py`, add `PERSON_DETAIL` beside the two
existing path constants at `:53-54`:

```python
MOVIE_CREDITS = "/person/4242/movie_credits"
TV_CREDITS = "/person/4242/tv_credits"
PERSON_DETAIL = "/person/4242"
```

Add a `_detail` helper beside `_credits` (`:79-81`) and put the detail route into
both existing helpers, so every test in the file exercises the real pair of
requests rather than a half-routed build:

```python
def _detail(**overrides):
    """The person-detail fixture, with fields replaced for the shape tests."""
    return load("tmdb_person_detail.json") | overrides


def _credits(**overrides):
    """The movie-credits fixture, with whole arrays replaced for the shape tests."""
    return {
        MOVIE_CREDITS: load("tmdb_person_movie_credits.json") | overrides,
        PERSON_DETAIL: _detail(),
    }


def _both():
    return {
        MOVIE_CREDITS: load("tmdb_person_movie_credits.json"),
        TV_CREDITS: load("tmdb_person_tv_credits.json"),
        PERSON_DETAIL: _detail(),
    }
```

Then the transport tests, in a new `# --- the person's own record ---` section
placed after the existing transport section:

```python
async def _detail_of(routes: dict):
    async with httpx.AsyncClient(transport=_routed(routes)) as http:
        return await TmdbListClient("a-read-access-token", http).person_detail(4242)


async def test_person_detail_carries_the_biography_and_the_profile_path():
    detail = await _detail_of({PERSON_DETAIL: _detail()})

    assert detail.biography.startswith("A constructed biography.")
    assert detail.profile_path == "/a-constructed-profile-path.jpg"


async def test_person_detail_carries_nothing_else():
    """The same rule ``PersonCredit`` keeps, for the same reason. The payload
    also holds ``birthday``, ``deathday``, ``place_of_birth``, ``also_known_as``
    and ``popularity``; every one of them is a feature nobody has decided on
    (birthday gating is roadmap row 160's filed work), and a field nobody reads
    is a field a builder can start reading without the decision being made."""
    assert set(PersonDetail.__dataclass_fields__) == {"biography", "profile_path"}


async def test_an_empty_biography_is_none_rather_than_an_empty_summary():
    """TMDb answers ``""`` -- not null -- for a person with no biography in the
    requested language. Passed through, that is a collection summary set to the
    empty string, which reads as "somebody chose this" one layer down."""
    detail = await _detail_of({PERSON_DETAIL: _detail(biography="   ")})

    assert detail.biography is None


async def test_a_missing_profile_photo_is_none_rather_than_a_guessable_path():
    detail = await _detail_of({PERSON_DETAIL: _detail(profile_path=None)})

    assert detail.profile_path is None


async def test_a_response_with_no_name_is_refused():
    """The response-shape probe. TMDb's person record always carries a name; a
    payload without one is not "a person with no biography", it is a response
    this client cannot read -- and the two must not be the same answer, because
    the first quietly leaves a collection without artwork forever."""
    with pytest.raises(TmdbListRefused, match="'name'"):
        await _detail_of({PERSON_DETAIL: {"id": 4242}})


async def test_an_unknown_person_id_raises_rather_than_returning_nothing():
    with pytest.raises(TmdbListRefused, match="404"):
        await _detail_of({})
```

Add `PersonDetail` to the module's import from `autoposter.providers.tmdb_lists`
(`:47`).

- [ ] **Step 3: Run them to see them fail**

```bash
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_person.py 2>&1 | tee /app/.superpowers/run-p10ct2-red1.log; echo EXIT=$?'
```

Expected: collection error — `ImportError: cannot import name 'PersonDetail'
from 'autoposter.providers.tmdb_lists'`. Paste it into the report.

- [ ] **Step 4: Implement the transport**

In `src/autoposter/providers/tmdb_lists.py`, after `PersonCredit` (`:130`):

```python
@dataclass(frozen=True)
class PersonDetail:
    """``/person/{id}``, reduced to the two fields a collection can use.

    ``PersonCredit``'s discipline, one endpoint along: the payload also carries
    ``birthday``, ``deathday``, ``place_of_birth``, ``also_known_as``,
    ``popularity`` and ``known_for_department``, and none of them crosses.
    Birthday/deathday gating is filed (roadmap row 160), and a field nobody
    reads is a field a builder can start reading without the decision being
    made.

    ``name`` is deliberately NOT here even though the client checks it: it is
    the response-shape probe (see ``person_detail``), not something any caller
    needs, and carrying it would make an unused field look like a considered
    one.

    Both fields are ``None`` for absence, and ``person_detail`` normalises the
    two shapes TMDb uses -- ``""`` for a biography it has none of in the
    requested language, ``null`` for a missing photo -- onto that one.
    """

    biography: str | None
    profile_path: str | None
```

And the method, after `person_credits` (`:356`):

```python
    async def person_detail(self, person_id: int) -> PersonDetail:
        """One person's own record: their biography and their profile photo.

        A second endpoint rather than a richer ``person_credits``, because the
        credits endpoints do not carry a biography at all -- ``/person/{id}``
        is where TMDb keeps it -- and because the two answer questions that
        change at different rates: a filmography changes when someone works, a
        biography changes about never, and two endpoints are two cache entries
        rather than one that has to expire for the faster of them.

        Not paged, like ``/collection/{id}``, so this sends no ``page``.

        The name is read and thrown away on purpose. It is the only field TMDb
        always sends for a person, so it is the one thing that distinguishes
        "this person has no biography and no photo" -- a legitimate answer that
        must leave the collection alone -- from "this response is not a person
        record", which must be refused. Without the probe the two are the same
        empty ``PersonDetail`` and the second one silently leaves a collection
        without artwork on every pass.
        """
        subject = f"TMDb person {person_id}"
        payload = await self._get(f"/person/{person_id}", {}, subject)
        if not isinstance(payload.get("name"), str) or not payload["name"]:
            raise TmdbListRefused(
                f"{subject}: TMDb's response carries no 'name', so it is not a "
                "person record. Reading that as 'this person has no biography "
                "and no photo' would leave the collection without artwork on "
                "every pass, with nothing to notice. Check the person id."
            )
        biography = payload.get("biography")
        profile_path = payload.get("profile_path")
        return PersonDetail(
            biography=biography.strip() if isinstance(biography, str) and biography.strip() else None,
            profile_path=(
                profile_path
                if isinstance(profile_path, str) and profile_path.startswith("/")
                else None
            ),
        )
```

Add `"PersonDetail"` to `__all__` (`:94-101`), in its sorted position between
`"MAX_PAGES"` and `"PersonCredit"`.

- [ ] **Step 5: Run them to see them pass**

```bash
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_person.py 2>&1 | tee /app/.superpowers/run-p10ct2-green1.log; echo EXIT=$?'
```

Expected: every transport test passes. The two fence tests (`:361`, `:421`) are
still green at this point — nothing has changed what a builder returns yet.

- [ ] **Step 6: Commit the transport**

```bash
git add src/autoposter/providers/tmdb_lists.py tests/test_builder_tmdb_person.py \
    tests/fixtures/collections/tmdb_person_detail.json
git commit --no-gpg-sign -m "feat(tmdb): read a person's own record, two fields and a shape probe"
```

- [ ] **Step 7: Write the failing poster-source tests**

In `tests/test_collection_poster_apply.py`, add a section. Read the file's
existing `apply_poster` call sites first (`:91`, `:115`, …) and reuse whatever
fixture/double it already builds — do not invent a second one:

```python
def test_a_tmdb_profile_path_becomes_an_image_cdn_url():
    """The one poster source that is not a Kometa ``Default-Images`` path. The
    base is ``providers/tmdb.py``'s, the same one the artwork pipeline fetches
    every other TMDb image from, so a size change happens in one place."""
    assert tmdb_profile_url("/a-constructed-profile-path.jpg") == (
        "https://image.tmdb.org/t/p/original/a-constructed-profile-path.jpg"
    )


def test_a_profile_path_tmdb_did_not_shape_has_no_url():
    """Same rule as an unrecognised ``hosted_poster_url`` kind: a guessed URL
    404s and the collection quietly keeps no poster, which is harder to spot
    than an error. TMDb's file paths are rooted at ``/``."""
    assert tmdb_profile_url("") is None
    assert tmdb_profile_url("a-constructed-profile-path.jpg") is None


def test_the_profile_kind_is_not_a_row_of_the_hosted_table():
    """``hosted_poster_url`` must keep answering ``None`` for it -- if it ever
    grew a matching row, two functions would both claim the kind and the
    dispatch in ``apply_poster`` would silently stop mattering."""
    assert hosted_poster_url(TMDB_PROFILE_KIND, "/a-constructed-profile-path.jpg") is None
```

Then the behaviour test through `apply_poster` itself, modelled on the file's
existing hosted-default test (whatever it is called there — reuse its transport
double and its `ManagedCollection` record):

```python
async def test_apply_poster_fetches_a_profile_photo_from_the_image_cdn():
    """The end-to-end proof that the new source reaches the existing upload
    machinery: same fetch, same ``_is_image`` validation, same hash-compare,
    same ``poster_sha256`` write."""
    # ... build the same session/record/collection doubles the neighbouring
    # hosted-default test builds, with an httpx MockTransport that answers
    # "https://image.tmdb.org/t/p/original/a-constructed-profile-path.jpg"
    # with a real image and 404s everything else. The file already renders one
    # at :28 -- `Image.new("RGB", (4, 4), color).save(buffer, format="JPEG")`
    # -- so reuse that helper rather than building a second image.
    message = await apply_poster(
        session, http, config, collection, record, "Movies",
        TMDB_PROFILE_KIND, "/a-constructed-profile-path.jpg", dry_run=False,
    )

    assert "set the poster" in message
    assert record.poster_sha256


async def test_a_local_poster_still_wins_over_a_profile_photo():
    """The override order is the source's, not the kind's. An operator file at
    ``<assets_root>/<library>/<title>/poster.jpg`` beats every hosted source
    (``prioritize_assets: true`` in the tool being replaced), and adding a
    seventh source must not have carved out an exception."""
    # ... write a real image to the local candidate path, then:
    message = await apply_poster(
        session, http, config, collection, record, "Movies",
        TMDB_PROFILE_KIND, "/a-constructed-profile-path.jpg", dry_run=False,
    )

    assert "local file" in message
```

Import `TMDB_PROFILE_KIND`, `tmdb_profile_url` and `hosted_poster_url` at the top
of the file.

- [ ] **Step 8: Run them to see them fail**

```bash
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_poster_apply.py 2>&1 | tee /app/.superpowers/run-p10ct2-red2.log; echo EXIT=$?'
```

Expected: `ImportError: cannot import name 'tmdb_profile_url'`. Paste it.

- [ ] **Step 9: Implement the poster source**

In `src/autoposter/collections/posters.py`, add the import beside the existing
ones (`:29-34`):

```python
from autoposter.providers.tmdb import IMAGE_BASE
```

(No cycle: `providers/tmdb.py` imports only `providers.base`, `providers.cache`
and `providers.fetch`, and nothing under `collections/`.)

Add the constant beside `DEFAULT_IMAGES_BASE` (`:38`):

```python
# The one poster kind whose URL is not a ``Default-Images`` path. Named here and
# imported by the builder that emits it, so the producer and the consumer cannot
# drift into two spellings of one string.
TMDB_PROFILE_KIND = "tmdb_profile"
```

And the source function, immediately after `hosted_poster_url` (`:129`):

```python
def tmdb_profile_url(profile_path: str) -> str | None:
    """The image-CDN URL for one TMDb profile path, or ``None``.

    Deliberately not a row of ``hosted_poster_url``'s table. Every kind there is
    a ``Default-Images`` path built from a key THIS SERVICE chose -- a chart
    name, a ceremony year, a content-rating bucket -- and the whole reason that
    function can refuse an unrecognised kind is that it owns the vocabulary. A
    profile path is an opaque file path TMDb handed back on a person's own
    record; the two share nothing but returning a string, and one table holding
    both would have a column of curated folder names with a provider's opaque
    path in it.

    ``IMAGE_BASE`` is ``providers/tmdb.py``'s, the same base the artwork
    pipeline fetches every other TMDb image from, so the size segment is
    configured in one place rather than two.

    A path TMDb did not shape -- empty, or not rooted at ``/`` -- returns
    ``None`` by the same rule an unrecognised kind does: a guessed URL 404s and
    the collection quietly keeps no poster, which is harder to spot than an
    error.
    """
    if not profile_path or not profile_path.startswith("/"):
        return None
    return f"{IMAGE_BASE}{profile_path}"
```

Change the hosted branch inside `apply_poster` (`:340`) from

```python
        url = hosted_poster_url(kind, key)
```

to

```python
        # Two poster SOURCES now, dispatched on ``kind`` here rather than inside
        # ``hosted_poster_url`` -- see ``tmdb_profile_url`` for why a TMDb file
        # path is not a row of that table. Everything past this line is
        # unchanged: the same fetch, the same ``_is_image`` validation, the same
        # hash-compare and the same locked upload serve both.
        url = (
            tmdb_profile_url(key) if kind == TMDB_PROFILE_KIND
            else hosted_poster_url(kind, key)
        )
```

And update the module docstring's first line (`:3-6`), which currently says
"Two pure decisions":

```python
"""Choosing which poster a managed collection should carry.

Three pure decisions: which hosted URL a collection's default poster lives at
(``hosted_poster_url``), which TMDb image URL a person's profile path points to
(``tmdb_profile_url`` -- the one source that is not Kometa's ``Default-Images``,
and the reason is on the function), and whether the operator has placed a local
override that should win instead (``local_poster_path``). Fetching and uploading
is a separate, later step -- these three never make a network call.
```

- [ ] **Step 10: Run them to see them pass, plus the whole poster suite**

```bash
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_poster_apply.py tests/test_collection_poster_wiring.py tests/test_collection_awards.py 2>&1 | tee /app/.superpowers/run-p10ct2-green2.log; echo EXIT=$?'
```

Expected: all pass. `test_collection_poster_wiring.py` is in the list on
purpose — it pins that a separator never gets a poster, which is the property a
new dispatch branch could break.

- [ ] **Step 11: Commit the poster source**

```bash
git add src/autoposter/collections/posters.py tests/test_collection_poster_apply.py
git commit --no-gpg-sign -m "feat(posters): a TMDb profile photo is a poster source, beside the hosted defaults"
```

- [ ] **Step 12: Rewrite fence test B — the real red**

Replace `tests/test_builder_tmdb_person.py:417-431` entirely. The section
heading goes with it: it is no longer "the 10c boundary", it is the behaviour.

```python
# --- the person's biography and photo ----------------------------------------


@pytest.mark.parametrize("builder", PERSON_BUILDERS)
async def test_a_person_builder_takes_its_summary_and_poster_from_the_person(builder):
    """The first item off the 8c hard stop. The biography is the collection's
    summary and the TMDb profile photo is its poster -- for all five names,
    because all five are one build path and a person's own record has nothing to
    do with which credits the role table keeps."""
    result = await _build(builder, _both())

    assert result.summary.startswith("A constructed biography.")
    assert result.poster_kind == TMDB_PROFILE_KIND
    assert result.poster_key == "/a-constructed-profile-path.jpg"


async def test_the_poster_kind_is_the_one_the_poster_module_dispatches_on():
    """Producer and consumer, pinned to one constant. Two spellings of
    ``"tmdb_profile"`` would not fail anything: ``apply_poster`` would fall
    through to ``hosted_poster_url``, get ``None``, and report "no poster
    source" forever."""
    result = await _build("tmdb_actor", _both())

    assert tmdb_profile_url(result.poster_key) is not None


async def test_a_person_with_no_biography_and_no_photo_offers_neither():
    """Data, not failure. The definition's own ``summary:`` is still how an
    operator sets one, and a collection with no poster simply keeps none."""
    routes = _both() | {PERSON_DETAIL: _detail(biography="", profile_path=None)}
    result = await _build("tmdb_actor", routes)

    assert result.ids
    assert result.summary is None
    assert (result.poster_kind, result.poster_key) == (None, None)


async def test_a_dead_person_record_leaves_the_membership_built(caplog):
    """The containment, and it is the asymmetry this build path is built
    around. The credits ARE the membership, so a failure there raises -- an
    empty list one layer down means "remove every member". A biography and a
    poster are cosmetic, so the second endpoint being down must leave the
    collection reconciled and its artwork untouched, not fail the definition."""
    routes = {
        MOVIE_CREDITS: load("tmdb_person_movie_credits.json"),
        TV_CREDITS: load("tmdb_person_tv_credits.json"),
    }
    with caplog.at_level(logging.WARNING):
        result = await _build("tmdb_actor", routes)

    assert result.ids
    assert result.summary is None
    assert (result.poster_kind, result.poster_key) == (None, None)
    assert "tmdb_actor" in caplog.text


async def test_a_dead_credits_call_still_fails_the_definition():
    """The other half of the same sentence, pinned so a later hand cannot
    contain both reads with one ``try``."""
    with pytest.raises(TmdbListRefused):
        await _build("tmdb_actor", {PERSON_DETAIL: _detail()})


async def test_the_persons_own_record_is_read_once_per_build():
    """One extra request per definition, not one per credit."""
    seen: list = []
    await _build("tmdb_actor", _both(), seen)

    assert [_path(request) for request in seen].count(PERSON_DETAIL) == 1


async def test_the_persons_record_is_read_after_their_credits():
    """A wrong id fails once, on the membership call, rather than making a
    second doomed request first."""
    seen: list = []
    with pytest.raises(TmdbListRefused):
        await _build("tmdb_actor", {PERSON_DETAIL: _detail()}, seen)

    assert [_path(request) for request in seen] == [MOVIE_CREDITS]
```

Add to the file's imports: `TMDB_PROFILE_KIND` and `tmdb_profile_url` from
`autoposter.collections.posters`.

- [ ] **Step 13: Rewrite fence test A's docstring (FLAG 1) — assertion unchanged**

`tests/test_builder_tmdb_person.py:361-366`. The assertion stays exactly as it
is; only the docstring moves:

```python
def test_a_credit_carries_only_what_the_role_table_reads():
    """The transport's return type as the enforcement, rather than everyone
    remembering. It matters MORE now that a person's biography and photo do
    ship: they come from the person's own record (``person_detail``), which is
    one call and one cache entry, and a credit entry also carries a profile
    path of its own. Widening this dataclass is how a later hand would end up
    reading artwork off whichever credit happened to be first."""
    assert set(PersonCredit.__dataclass_fields__) == {"tmdb_id", "kind", "job", "department"}
```

Also update the file's module docstring (`:26-32`) so the fixtures paragraph
names the new file: add one sentence saying `tmdb_person_detail.json` is the
same kind of construction — TMDb's response shape, a person who does not exist,
and the biography says so in its own text.

- [ ] **Step 14: Run to see the red**

```bash
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_person.py 2>&1 | tee /app/.superpowers/run-p10ct2-red3.log; echo EXIT=$?'
```

Expected: the seven new behaviour tests fail — the parametrized one with
`AssertionError` on `result.summary` being `None`, the containment ones on the
same. **Paste the full failure list.** This is the deliberate break-site C2
named; the report must show it happening rather than assert that it did.

- [ ] **Step 15: Implement the builder change**

In `src/autoposter/collections/builders/tmdb_person.py`, add the import beside
the existing ones (`:98-99`):

```python
from autoposter.collections.posters import TMDB_PROFILE_KIND
```

Replace the tail of `_TmdbPersonBuilder.build` (`:194-201`) and add `_profile`
after it:

```python
        if not ids:
            logger.warning(
                "%s: TMDb person %d has no %s among the %d credit(s) it lists for "
                "this library's media type, so this collection would be emptied. "
                "Check the person id and that this role is one they hold.",
                self.type_name, params.id, role.described(), len(credits),
            )

        summary, profile_path = await self._profile(client, params.id)
        return BuilderResult(
            ids=[("tmdb", value) for value in ids],
            summary=summary,
            poster_kind=TMDB_PROFILE_KIND if profile_path else None,
            poster_key=profile_path,
        )

    async def _profile(self, client, person_id: int) -> tuple[str | None, str | None]:
        """The person's biography and profile path, or ``(None, None)``.

        CONTAINED, unlike the credits read above, and the asymmetry is the
        point. The credits ARE this collection's membership: a failure there
        has to raise, because an empty list one layer down means "remove every
        member" (``lists.reconcile_list_collection``). A biography and a poster
        are cosmetic -- ``posters.apply_poster`` says exactly that for every
        other source -- so a second endpoint being down must leave the
        membership reconciled and the artwork untouched rather than fail the
        definition over an ornament.

        Read AFTER the credits, so a wrong id fails once on the membership call
        instead of making a second doomed request first.

        ``poster_kind`` is left None when there is no photo rather than being
        set with an empty key: ``apply_poster`` would resolve that to no URL
        and report "no poster source" on every pass, which is a line in every
        report for a person who simply has no photo.
        """
        try:
            detail = await client.person_detail(person_id)
        except Exception:
            logger.exception(
                "%s: could not read TMDb person %d's own record; the collection "
                "keeps whatever summary and poster it already has",
                self.type_name, person_id,
            )
            return None, None
        return detail.biography, detail.profile_path
```

- [ ] **Step 16: Update the hard-stop docstring in the same commit (C2)**

`tmdb_person.py:64-83`. The first deferred item is being lifted, so it moves out
of the absent list and into a delivered note. Replace the block's opening and its
first bullet:

```
**Deliberately absent -- this is the 10c line, and it is still a hard stop.**
Phase 10c-lite lifted the FIRST item off this list: ``tmdb_person`` -- the
person's biography as the collection summary and their TMDb profile photo as
its poster -- ships, through ``TmdbListClient.person_detail`` and the
``tmdb_profile`` poster source (``collections/posters.py``). The read is
contained (see ``_profile``): a dead person record leaves the membership
reconciled. Everything below is still 10c's and none of it belongs here:

- ``tmdb_popular_people``: TMDb's popular-people list as a source.
- ``tmdb_birthday`` / ``tmdb_deathday``: gating whether a definition runs at
  all on a date derived from the person. Filed on roadmap row 160, with the
  day-level window machinery it is really asking for; ``person_detail``
  deliberately does not carry ``birthday``/``deathday`` out of the transport.
- appearance thresholds and the dynamic ``actor``/``director``/``writer``/
  ``producer`` collection *types* -- "every actor with at least N titles in
  this library" -- which need the library-wide credit scan below. Roadmap row
  194, and gated twice over: the scan, and row 169's people search attributes.
- library-wide credit scans: enumerating every credit of every item in a
  library. That is 10c's expensive piece and its caching is a design decision
  the row records; this module reads one person, by id, in two requests.

The roadmap's row 83 lists the summaries and the birthday gating alongside
these builders; it CLOSED with 10c-lite, and row 194 is where the expensive
half went.
```

**If Task 3 ships (branch A), that second bullet is removed in Task 3's own
commit, not here.** If Task 3 files instead, the bullet gains "Roadmap row 194"
in Task 4. Task 3 and Task 4 each say so at the point they do it.

- [ ] **Step 17: Run to see green**

```bash
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_person.py tests/test_collection_poster_apply.py tests/test_builder_tmdb.py tests/test_builder_engine.py 2>&1 | tee /app/.superpowers/run-p10ct2-green3.log; echo EXIT=$?'
```

Expected: all pass, `EXIT=0`.

- [ ] **Step 18: The golden gate, for real (global constraint 5)**

```bash
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-p10ct2-golden.log; echo EXIT=$?'
git status --porcelain tests/fixtures/collections/golden_port.json
```

Expected: pass, and the `git status` line is EMPTY. Paste both. `apply_poster`
gained a branch that only fires on one kind no golden scenario emits — this run
is what turns that from an argument into evidence.

- [ ] **Step 19: Mutation proof — the containment is real**

Prove `_profile`'s `except` is load-bearing, not decoration:

```bash
cp src/autoposter/collections/builders/tmdb_person.py /tmp/tp.bak
# edit: delete the try/except in _profile, calling client.person_detail directly
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_person.py -k dead_person_record 2>&1 | tail -20'
cp /tmp/tp.bak src/autoposter/collections/builders/tmdb_person.py
cmp /tmp/tp.bak src/autoposter/collections/builders/tmdb_person.py && echo RESTORED-EXACT
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_person.py -k dead_person_record 2>&1 | tail -5'
```

Paste both halves plus `RESTORED-EXACT`.

- [ ] **Step 20: Mutation proof — fence test A still has teeth (FLAG 1)**

The test whose assertion did not change still has to be shown to bite, because
this plan is claiming it is worth keeping:

```bash
cp src/autoposter/providers/tmdb_lists.py /tmp/tl.bak
# edit: add `profile_path: str | None = None` to PersonCredit
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_person.py -k only_what_the_role_table_reads 2>&1 | tail -20'
cp /tmp/tl.bak src/autoposter/providers/tmdb_lists.py
cmp /tmp/tl.bak src/autoposter/providers/tmdb_lists.py && echo RESTORED-EXACT
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_person.py -k only_what_the_role_table_reads 2>&1 | tail -5'
```

Paste both halves.

- [ ] **Step 21: Ruff, then the full suite detached**

```bash
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'ruff check src tests 2>&1 | tee /app/.superpowers/run-p10ct2-ruff.log'
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    up -d postgres
docker compose -p p10ct2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10ct2-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-p10ct2-full.log; echo EXIT=$? >> /app/.superpowers/run-p10ct2-full.log'
docker wait p10ct2-full
tail -30 .superpowers/run-p10ct2-full.log
```

Expected: ruff clean, suite green. If `tests/test_worker.py` fails, that is
**roadmap row 193's known intermittent** — re-run the file alone, record both
outcomes and the log paths in the report, and add the sighting to row 193 in
Task 4. Do not "fix" it here.

- [ ] **Step 22: Commit the builder change**

```bash
git add src/autoposter/collections/builders/tmdb_person.py tests/test_builder_tmdb_person.py
git commit --no-gpg-sign -m "feat(collections): person collections carry the person's bio and photo"
docker compose -p p10ct2 down
```

---

## Task 3: `tmdb_popular_people` — ship it, or file it honestly

**The decision gate. Read this before anything else in this task.**

Open `.superpowers/sdd/p10c-upstream-person.md` §3 and read its verdict line.

- `VERDICT B-GATED` → **do Branch B only.** Skip every Branch A step. This is
  not a failure of the phase; it is C3's stated outcome ("Honest outcome over
  forced shipping"), and Branch B is roughly forty minutes of paperwork.
- `VERDICT A-EXPAND` → **do Branch A, expand shape.**
- `VERDICT A-SINGLE` → **do Branch A, single-collection shape.**
- No verdict line, or a verdict the record does not support with a citation →
  **Branch B.** STOP and say so in the report.

Whichever branch runs, Task 4 must be told which, because Task 4's roadmap row
and preset descriptions differ.

---

### Branch B — file it (the default, and the outcome if T1 could not settle it)

**Files:** none in this task. Branch B's whole content is two paragraphs that
Task 4 writes into row 194 — this step exists so Task 3 has a deliverable and a
report even when nothing ships.

- [ ] **B1: Write the filing paragraph into the task report**

Two paragraphs, which Task 4 copies verbatim into row 194:

1. **What upstream's shape is**, with its `meta.py` citation from §3, and the
   specific clause that needs the Plex actor tag.
2. **Why that gates it here**: the actor tag is roadmap row 169's machinery
   (`actor`/`director`/`producer`/`writer` are not in `FILTER_ATTRIBUTES`, so
   `filters.BY_NAME` has no row for them and `plex_search` cannot express the
   query at all), and row 169 needs more than a table row — Kometa resolves a
   person through `get_actor_id`, which falls back to `hubSearch` when the name
   is not already in the section's tag list, a second lookup mechanism with a
   second failure mode (found in the hub, absent from this section).
3. One sentence on **what could ship without it and deliberately does not**:
   an expansion into per-person `tmdb_actor` filmographies is buildable today
   and would be a DIFFERENT membership from upstream's — upstream's collection
   holds the library items tagged with that actor, ours would hold the library
   items TMDb credits them on — so shipping it under upstream's name would be
   the fabricated attribution global constraint 2 forbids. If it ever ships it
   ships as ours, `NOT_KOMETA`, with that difference in the row.

- [ ] **B2: Confirm no code changed**

```bash
git status --porcelain
```

Expected: EMPTY. Report it. Then go to Task 4.

---

### Branch A — ship it

**Files:**
- Modify: `src/autoposter/providers/tmdb_lists.py` (`PopularPerson`,
  `popular_people`, `__all__`)
- Create: `src/autoposter/collections/builders/tmdb_popular_people.py`
- Modify: `src/autoposter/collections/builders/__init__.py` (one import, one
  `register`)
- Modify: `src/autoposter/collections/builders/tmdb_person.py` (delete the
  `tmdb_popular_people` bullet from the hard-stop list — Task 2 Step 16 left it
  there for exactly this)
- Create: `tests/fixtures/collections/tmdb_popular_people_p1.json`,
  `tests/fixtures/collections/tmdb_popular_people_p2.json`
- Create: `tests/test_builder_tmdb_popular_people.py`

**Interfaces:**
- Consumes: `TmdbListClient._get`, `_TmdbBuilder._client`,
  `PersonDetail`/`person_detail` (Task 2), `TMDB_PROFILE_KIND` (Task 2),
  `require_library_type`, `CollectionDefinition`
  (`autoposter.config.schema`), and — for A-EXPAND — the `expand` protocol at
  `engine.py:172-190` plus the `TITLE_PATTERN` convention at `engine.py:979-985`.
- Produces: `providers.tmdb_lists.PopularPerson(tmdb_id: int, name: str)`;
  `async TmdbListClient.popular_people(limit: int) -> list[PopularPerson]`;
  a builder registered as `tmdb_popular_people`.

- [ ] **A1: Write the fixtures**

Two pages of `/person/popular`, constructed, in TMDb's shape. Page 1 carries
`page`, `total_pages: 2`, `total_results`, and a `results` array whose entries
carry `id`, `name`, `popularity`, `profile_path`, `known_for_department` and
`known_for` (an array of title objects with `id` and `media_type`). Page 2
carries two more. **Constructed names only** — "A Constructed Person", "Another
Constructed Person" — for the reason `test_builder_tmdb_person.py:26-32` gives:
a fixture that looks like a recording of a real popularity ranking is one, and
this one is not.

If §3's verdict is **A-SINGLE**, the `known_for` arrays are the membership and
must carry real-shaped `id`/`media_type` pairs including one `"tv"` entry, so
the media-type filter is exercised.

- [ ] **A2: Write the failing transport test**

In a new `tests/test_builder_tmdb_popular_people.py`, using the same
`_routed`/`_path` helpers `test_builder_tmdb_person.py` uses (copy them; the
suite's existing files each carry their own — do not build a shared conftest
helper for two callers):

```python
async def test_popular_people_reads_pages_in_order_until_it_has_enough():
    """Rank order is the whole answer here: "the ten most popular people" is a
    prefix of TMDb's own ordering, so a page loop that reordered or started at
    page two would build a plausible, wrong family."""
    seen: list = []
    async with httpx.AsyncClient(transport=_routed(POPULAR_ROUTES, seen)) as http:
        people = await TmdbListClient("a-read-access-token", http).popular_people(3)

    assert [person.name for person in people] == [
        "A Constructed Person", "Another Constructed Person", "A Third Constructed Person",
    ]
    assert len(seen) == 1, "three from a twenty-per-page endpoint is one request"


async def test_popular_people_follows_a_page_when_one_is_not_enough():
    async with httpx.AsyncClient(transport=_routed(POPULAR_ROUTES)) as http:
        people = await TmdbListClient("a-read-access-token", http).popular_people(6)

    assert len(people) == 6


async def test_popular_people_stops_at_the_last_page_rather_than_looping():
    async with httpx.AsyncClient(transport=_routed(POPULAR_ROUTES)) as http:
        people = await TmdbListClient("a-read-access-token", http).popular_people(999)

    assert len(people) == 6  # both fixture pages, then TMDb's own total_pages


async def test_a_popular_entry_with_no_name_is_refused_rather_than_dropped():
    """A dropped entry is a silently shorter family and no way to notice."""
    with pytest.raises(TmdbListRefused, match="'name'"):
        ...
```

- [ ] **A3: Run it red, then implement the transport**

```bash
docker compose -p p10ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_popular_people.py 2>&1 | tee /app/.superpowers/run-p10ct3-red1.log; echo EXIT=$?'
```

Then, in `providers/tmdb_lists.py`:

```python
@dataclass(frozen=True)
class PopularPerson:
    """One entry of ``/person/popular``, reduced to the two fields a family
    needs: who they are, and what to call their collection.

    Same rule as ``PersonCredit`` and ``PersonDetail``. The entry also carries
    ``popularity``, ``profile_path``, ``known_for_department`` and a
    ``known_for`` array; the popularity value is not carried because the ORDER
    already expresses it and a second expression of one fact is a second thing
    to keep in step.
    """

    tmdb_id: int
    name: str


    async def popular_people(self, limit: int) -> list[PopularPerson]:
        """The most popular people TMDb knows, in TMDb's own rank order.

        Paged like the charts, and bounded twice: by ``limit`` (what the
        definition asked for) and by ``self._max_pages`` (what this client will
        ever spend on one answer). Stops on the page past the end and on
        ``total_pages``, exactly as ``_paged`` does -- it is not ``_paged``
        itself because that helper returns bare ids and this endpoint's answer
        is people, whose names become collection titles.
        """
        people: list[PopularPerson] = []
        subject = "TMDb's popular people"
        for page in range(1, self._max_pages + 1):
            payload = await self._get("/person/popular", {"page": page}, subject)
            entries = payload.get("results")
            if not isinstance(entries, list):
                raise TmdbListRefused(
                    f"{subject}: TMDb's response carries no 'results' array"
                )
            if not entries:
                break
            for entry in entries:
                person_id = entry.get("id") if isinstance(entry, dict) else None
                name = entry.get("name") if isinstance(entry, dict) else None
                if not isinstance(person_id, int) or not isinstance(name, str) or not name:
                    raise TmdbListRefused(
                        f"{subject}: an entry carries no 'id' or no 'name'. Dropping "
                        "it would make the family silently one collection shorter "
                        "than it asked for."
                    )
                people.append(PopularPerson(tmdb_id=person_id, name=name))
                if len(people) >= limit:
                    return people
            total_pages = payload.get("total_pages")
            if isinstance(total_pages, int) and page >= total_pages:
                break
        return people
```

Add `"PopularPerson"` to `__all__`.

- [ ] **A4: Write the failing builder tests, then the builder — A-EXPAND shape**

*(Do A5 instead if the verdict is A-SINGLE.)*

The builder expands, `ImdbAwardYearsBuilder`'s precedent
(`builders/imdb_award.py:666-718`), and therefore needs a `TITLE_PATTERN` so
`engine.definition_titles` (`engine.py:979-985`) can recognise the family's
collections without re-fetching. Title format comes from §3; if §3 records
none, it is `NOT KOMETA` and the row says so.

Tests first:

```python
async def test_it_expands_to_one_definition_per_popular_person():
    units = await _expand(limit=3)

    assert [unit.title for unit in units] == [
        "A Constructed Person (Actor)",
        "Another Constructed Person (Actor)",
        "A Third Constructed Person (Actor)",
    ]
    assert [unit.params["id"] for unit in units] == [4242, 4243, 4244]
    assert {unit.builder for unit in units} == {"tmdb_actor"}


async def test_every_expanded_title_is_matched_by_the_family_pattern():
    """``engine.definition_titles`` recognises this family's collections by
    ``TITLE_PATTERN`` with no definition in hand -- a pattern that missed one
    would make the delete sweep read a collection this family built as a prior
    tool's leftover."""
    units = await _expand(limit=3)
    pattern = REGISTRY["tmdb_popular_people"].TITLE_PATTERN

    assert all(pattern.match(unit.title) for unit in units)


async def test_the_family_refuses_a_library_type_it_cannot_mean():
    """Gated at expand, the earliest point that knows the library type and the
    only path to ``build`` -- so a refusal costs no fetch and leaves nothing
    half-done (``imdb_award.py:691-698``'s argument, unchanged)."""
    ...


async def test_a_definition_asking_for_more_people_than_tmdb_has_gets_what_exists():
    ...


async def test_the_expanded_definitions_carry_the_placeholders_settings():
    """``engine._completed`` fills them in (roadmap row 141); this pins that a
    ``labels:``/``limit:`` on the placeholder is not silently dropped."""
    ...
```

Then `src/autoposter/collections/builders/tmdb_popular_people.py`. It reuses
`_TmdbBuilder` for the absent-client refusal (as `tmdb_person.py:98` does),
declares `params_model` with `extra="forbid"`, gates on library type in
`expand`, and has **no `build` of its own** — every unit it emits names an
existing person builder, so there is nothing new to build. Its module docstring
must carry: which §3 finding each value came from, the `NOT_KOMETA` marks for
anything §5 lists, and why the per-person collection is a `tmdb_actor`
filmography rather than upstream's actor-tag search (if that is the divergence —
in which case the divergence is stated in the docstring AND in Task 4's row).

- [ ] **A5: Write the failing builder tests, then the builder — A-SINGLE shape**

*(Do A4 instead if the verdict is A-EXPAND.)*

One collection, membership = the `known_for` titles of the top N people, in
order, deduped, filtered to this library's media type. It is an ordinary
`build`, closest to `TmdbListBuilder` (`builders/tmdb.py`) which is the other
builder answering with both media types.

```python
async def test_it_builds_one_collection_of_the_popular_peoples_known_for_titles():
    result = await _build_popular(library_type="Movie", limit=3)

    assert _ids(result) == ["11", "1891", "12"]


async def test_a_show_library_gets_the_television_half_and_not_the_films():
    """TMDb's two id spaces share one namespace, so a show id resolved against
    a Movie library can land on an unrelated film -- ``tmdb_list``'s hazard,
    one endpoint along (``builders/tmdb.py:17-21``)."""
    result = await _build_popular(library_type="Show", limit=3)

    assert _ids(result) == ["1399"]


async def test_a_title_two_popular_people_are_both_known_for_appears_once():
    """At its first position: order becomes the collection's custom order."""
    ...


async def test_the_collection_takes_no_summary_and_no_poster_from_a_person():
    """Deliberate, and the opposite of the five filmography builders: this
    collection is not ABOUT one person, so there is no person whose biography
    or photo could honestly describe it."""
    result = await _build_popular(library_type="Movie", limit=3)

    assert result.summary is None
    assert (result.poster_kind, result.poster_key) == (None, None)
```

- [ ] **A6: Register it, and remove the bullet Task 2 left**

`builders/__init__.py`: one import in the sorted import block, one `register(...)`
placed after the five person registrations at `:114-118`, under a one-line
comment saying what it is.

`builders/tmdb_person.py`: delete the `tmdb_popular_people` bullet from the
hard-stop list — Task 2 Step 16 left it there for this. The list is now three
items.

- [ ] **A7: Green, golden, ruff, full suite**

```bash
docker compose -p p10ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_tmdb_popular_people.py tests/test_builder_tmdb_person.py tests/test_config_schema.py tests/test_builder_engine.py 2>&1 | tee /app/.superpowers/run-p10ct3-green1.log; echo EXIT=$?'
docker compose -p p10ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-p10ct3-golden.log; echo EXIT=$?'
git status --porcelain tests/fixtures/collections/golden_port.json
docker compose -p p10ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'ruff check src tests 2>&1 | tee /app/.superpowers/run-p10ct3-ruff.log'
docker compose -p p10ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    up -d postgres
docker compose -p p10ct3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10ct3-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-p10ct3-full.log; echo EXIT=$? >> /app/.superpowers/run-p10ct3-full.log'
docker wait p10ct3-full
tail -30 .superpowers/run-p10ct3-full.log
```

- [ ] **A8: Mutation proof — the family pattern**

The `TITLE_PATTERN` (A-EXPAND) or the media-type filter (A-SINGLE) is the one
silent-wrong-answer risk in this builder. Break it, watch the test go red,
restore with `cmp`, watch it go green. Paste both halves.

- [ ] **A9: Commit**

```bash
git add src/autoposter/providers/tmdb_lists.py \
    src/autoposter/collections/builders/tmdb_popular_people.py \
    src/autoposter/collections/builders/__init__.py \
    src/autoposter/collections/builders/tmdb_person.py \
    tests/test_builder_tmdb_popular_people.py \
    tests/fixtures/collections/tmdb_popular_people_p1.json \
    tests/fixtures/collections/tmdb_popular_people_p2.json
git commit --no-gpg-sign -m "feat(collections): tmdb_popular_people, in the shape upstream ships"
docker compose -p p10ct3 down
```

---

## Task 4: The wrap — one new row, one closed row, five citations, four descriptions

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`
- Modify: `src/autoposter/collections/catalog.py`
- Modify: `src/autoposter/collections/dynamic_types.py`
- Modify: `tests/test_collection_catalog.py`
- Create: `.superpowers/sdd/p10c-pr-body.md`

**Interfaces:**
- Consumes: Task 1's record (§2 for what a people pack's poster would be, §3 for
  what popular-people turned out to be, §4 for the birthday sentence, §5 for
  what is ours); Task 2's shipped names (`person_detail`, `TMDB_PROFILE_KIND`);
  Task 3's branch and its report.
- Produces: roadmap row **194**; `catalog.PERSON_SCAN_ROW = 194`; row 83 closed.

**The row number is 194 and it was verified, not assumed.** The roadmap's gap
table is read by `tests/test_collection_catalog.py:79-91`, which regex-matches
`^\|\s*(\d+)\s*\|` over the whole document; row **193** (`tests/test_worker.py`
intermittent) is the highest numbered row at `origin/main`. Re-verify before
writing:

```bash
grep -oE '^\| [0-9]+ \|' docs/superpowers/specs/2026-08-22-full-parity-roadmap.md \
  | grep -oE '[0-9]+' | sort -n | tail -3
```

If it prints anything above 193, use the next free number everywhere below and
say so in the report.

- [ ] **Step 1: Write roadmap row 194**

Append after row 193 (`:289`), in the table's own format
(`| N | title | body | size | evidence | deps |`). It is graded against row 192,
which is the standard C1 set: a row a reader can act on without this session.
It must contain, each as its own clause:

1. **Filed by phase 10c-lite, out of adjudication C1**, because row 83 split:
   the static half shipped in 8c, the bio/poster half shipped here, and the
   expensive half never got the argument row 192 got.
2. **What upstream's packs actually are**, from Task 1 §2 and
   `catalog.py:1661-1665`: `dynamic_collections` with `data: {depth: 5, limit:
   25}` — one collection per person with at least five appearances, capped at
   twenty-five, and **the packs name no people at all**, which is why there is
   nothing to transcribe and why `people_directors` ships six directors that are
   openly ours.
3. **Blocker one — row 169, and it is STRUCTURAL, not merely missing.**
   `actor`, `director`, `writer` and `producer` are not rows of
   `FILTER_ATTRIBUTES`, so `filters.BY_NAME` (`filters.py:890`, built from that
   table) has no entry and the smart query cannot be written at all. Row 169
   needs more than four table rows: Kometa resolves a person through
   `get_actor_id`, falling back to `hubSearch` when the name is not already in
   the section's tag list — a second lookup mechanism with a second failure
   mode (found in the hub, absent from this section).
4. **Blocker two — the credit scan, which is the rows 155/180 budget family.**
   Naming the people means reading every credit of every item in the library:
   a Plex-side per-item people read, through a cache that does not exist yet
   (`item_facts`, or a table of its own — the design decision, stated as a
   decision and not as a detail).
5. **The counts problem no choices listing can answer.** `depth` is a count of
   appearances. `listFilterChoices` returns VALUES, never counts, so even a
   library whose people were enumerable would not answer the question the pack
   asks. This is the sentence that distinguishes this row from rows 189/192,
   where the enumeration alone was the blocker.
6. **The fan-out**, against the number the 10a probe actually measured: the
   worst measured enumeration is `studio` at **824** values
   (`docs/research/plex-dynamic-probe/README.md`), and a library's distinct
   cast is larger than its distinct studios by an order of magnitude — so this
   family meets `params.max_collections`' refusal (which creates nothing and
   reports both numbers) rather than a truncation.
7. **The smart-vs-manual divergence question, stated as an open question.**
   Upstream's per-person collection is a search on the Plex ACTOR TAG — the
   library's own credit data. A `tmdb_actor` filmography is TMDb's credit data
   intersected with the library. They are different memberships, and which one
   this service should build is a decision nobody has made. Whoever closes this
   row makes it explicitly.
8. **What 10c-lite DID ship**, so the row is not read as "people do not work":
   the five filmography builders (8c) now carry the person's TMDb biography as
   their summary and their profile photo as their poster
   (`TmdbListClient.person_detail`, `posters.tmdb_profile_url`), and
   `people_directors` is a working preset.
9. **`tmdb_popular_people`'s disposition**, from Task 3's branch — either
   "shipped in phase 10c-lite, see row 83" or the Branch B filing paragraphs
   verbatim.
10. Size / evidence / deps columns: **`L — a budgeted per-item credit scan with
    its own cache, then row 169's person lookup, then one dynamic-type row`** /
    `parity-only` / `169, 155/180, 102 (engine, closed)`.

- [ ] **Step 2: Close row 83 and re-point its `Detail:` (C6a)**

Row 83 (`:179`). Title becomes
`Person builders (CLOSED — phase 10c-lite)`. Append an
`**answered 10c-lite:**` clause saying, in this order:

- what shipped (bio → summary, profile photo → poster, on all five builders,
  through one new `/person/{id}` transport method and one new poster source that
  is deliberately not a row of the `Default-Images` table);
- that the read is contained — a dead person record leaves the membership
  reconciled, because the credits are the membership and the biography is an
  ornament;
- `tmdb_popular_people`'s disposition (Task 3's branch);
- that birthday/deathday gating went to **row 160** and the appearance-threshold
  dynamic types went to **row 194**;
- and the `Detail:` correction. The existing `Detail:
  .superpowers/sdd/task-2-report.md` points at what is now **10b's** Task 2
  report; 8c's was archived. Re-point it to
  `.superpowers/sdd/archive/p8c-task-2-report.md`, and add
  `.superpowers/sdd/p10c-upstream-person.md` beside it as this phase's own.

Verify the target exists before writing the citation:

```bash
ls -la .superpowers/sdd/archive/p8c-task-2-report.md
```

- [ ] **Step 3: File birthday/deathday on row 160 (C4)**

C4 licenses either the new row or row 160's family, "if the plan-writer finds
that fit stronger". **It is row 160**, and the reason is that birthday gating
has nothing to do with the credit scan: row 194 is about ENUMERATING people,
while a birthday gate is about WHEN a definition runs. Row 160 is already
exactly that — "Kometa windows its seasonal collections by DAY … a months-only
gate cannot express one" — and `ScheduleGate` (`config/schema.py`) is the object
both would extend. Filing it on 194 would bury a small scheduling feature inside
a large enumeration row where nobody would find it.

Append one sentence to row 160 (`:256`), before its final `|` columns:

> **Second consumer, filed by phase 10c-lite (adjudication C4):** Kometa's
> `tmdb_birthday`/`tmdb_deathday` gate a definition's RUN on a date derived from
> a person <one clause from Task 1 §4, with its citation>, which is the same
> day-granular window this row's first half is about, on a date that comes from
> a provider instead of a calendar. The data is one field away — `/person/{id}`
> carries `birthday` and `deathday`, and `providers/tmdb_lists.PersonDetail`
> deliberately does not carry them out of the transport — so what is missing is
> the day-level `ScheduleGate`, not the lookup.

- [ ] **Step 4: Re-scope the 10c roadmap section and fix its `Closes:` (C6b)**

`:943-955`. The **Closes:** line currently reads "row 83's dynamic half
(builders landed in 8c) and the person dynamic types of row 102" — and row 102
CLOSED in phase 10a-2, so it names finished work. Rewrite the section to the
split:

- **Goal:** unchanged in ambition, but split into what shipped and what did not.
- **Closes:** `row 83 (CLOSED by 10c-lite). The appearance-threshold dynamic
  person types are row 194.`
- **Size:** `10c-lite: small (shipped). The remainder: large.`
- **Risks:** keep the existing sentence about the expensive scan and its caching
  decision, and add that row 169 is a hard structural prerequisite discovered by
  the 10c recon — `BY_NAME[attribute]` has no row for the people tags today.
- **Testable when shipped:** keep the original line for the remainder, and add
  10c-lite's own: a `tmdb_director` definition builds a collection whose summary
  is the director's TMDb biography and whose poster is their profile photo,
  unless the definition sets its own summary or a local poster file exists.

- [ ] **Step 5: Fix line 902's carve-out (C6d — found while planning)**

`:902-903` reads "the four people packs among the eighteen are row 83's and
phase 10c's rather than this row's". Row 83 closes in this phase, so this
sentence would point at a closed row. Change "row 83's and phase 10c's" to
"row 194's". Nothing else in that paragraph moves.

This is a fourth stale citation of exactly the class C6 exists to prevent;
C6 named three because the recon found three. Flagged in the report.

- [ ] **Step 6: Give `dynamic_types.py`'s people absence its argument (C6c)**

`src/autoposter/collections/dynamic_types.py:38-39`. Today the people types
share a one-clause bullet with five unrelated things while every neighbour at
`:30-37` gets a per-type argument. Split them out:

```python
- the people types (``actor``/``director``/``writer``/``producer``) -- gated
  twice, and neither gate is an enumeration problem this module could solve.
  Upstream's pack is one collection per person with at least N appearances
  (``data: {depth: N}``), and ``listFilterChoices`` answers with VALUES, never
  COUNTS, so even an enumerable cast would not answer the question the pack
  asks; the counting is a library-wide credit scan, the rows 155/180 budget
  family. And the query each collection would carry cannot be written at all
  today: ``actor``/``director``/``writer``/``producer`` are not
  ``FILTER_ATTRIBUTES`` rows, so ``filters.BY_NAME`` has no entry for them
  (roadmap row 169). Roadmap row 194.
- ``tmdb_collection`` -- a full-library TMDb walk over every item's
  ``belongs_to_collection``, not an enumeration (roadmap row 192).
- ``trakt_*``, ``number``, ``custom`` and the music types -- none of them is a
  library enumeration.
```

`tmdb_collection` gets its own line here because row 192 already carries its
argument and a bullet naming it beside `trakt_*` was the same one-clause defect.

- [ ] **Step 7: Re-point the gated presets and correct their promise (C1, C5)**

In `src/autoposter/collections/catalog.py`:

Replace the constant at `:661`:

```python
PERSON_SCAN_ROW = 194      # the library-wide credit scan the four people packs
                           # need to NAME anyone, plus row 169's people search
                           # attributes, which the per-person query needs to
                           # exist at all. Row 83 (the person BUILDERS) closed
                           # in 10c-lite: the filmographies, their TMDb
                           # biographies and their profile photos all ship.
```

and its one read at `:1733` (`gated_row=PERSON_SCAN_ROW`). `PERSON_DYNAMIC_ROW`
has exactly one reader — verify with `grep -rn PERSON_DYNAMIC_ROW src/ tests/`
before and after; the rename must leave zero hits.

Rewrite the four gated rows' shared description (`:1724-1729`) so the gated
sentence names ONLY what still blocks them (C5). The current text promises "a
poster and biography from TMDb" and then blames the credit scan for the whole
thing; after 10c-lite the first half is built and the blocker is narrower:

```python
        description=(
            "One collection per person: %s. The poster and biography half is "
            "built -- a person collection takes its summary from their TMDb "
            "biography and its poster from their TMDb profile photo, which is "
            "what the Director starter set above does today. What is missing "
            "is NAMING the people: that means counting every credit of every "
            "item in the library, and then writing a per-person query out of "
            "search attributes this service does not have yet. Both are "
            "roadmap row %d." % (what, PERSON_SCAN_ROW)
        ),
```

Then add one sentence to `people_directors`' description (`:1699-1707`), because
what that preset builds visibly changed in this phase and the picker is where an
operator reads it:

```python
            "Each collection takes its summary from the director's TMDb "
            "biography and its poster from their TMDb profile photo; a "
            "`summary:` of your own, or a poster file in the assets folder, "
            "still wins."
```

Keep its existing "defaults/movie/director.yml names no directors at all"
sentence — it is the provenance argument and it is still true — but re-point its
"the Top directors row below waits on" clause to the credit scan and row 194 by
name, since "the row below" is now a row with a number.

- [ ] **Step 8: The catalog tests**

`tests/test_collection_catalog.py:1054-1067`,
`test_every_gated_row_cites_a_roadmap_row_that_exists`, asserts
`{96, 102, 155, 160, 161} <= rows` as its "the parse works" probe. Add the new
row to that probe so the parse is proven to see it:

```python
    assert {96, 102, 155, 160, 161, 194} <= rows
```

The `CATALOG_CHECKSUM` at `:265-275` does **not** move: `"people": (1, 4, 0)` is
one READY and four GATED, and this phase re-points a `gated_row` and rewrites
descriptions without adding or flipping a row. **If Task 3 shipped and added a
preset row, it moves — and Task 3 would have moved it in its own commit, not
here.** Confirm which is true before running.

`test_no_preset_still_waits_on_the_row_the_dynamic_engine_closed` (`:1070-1085`)
guards row 102 the way this phase now needs row 83 guarded. Add its sibling:

```python
def test_no_preset_still_waits_on_the_person_builders_row():
    """Row 83 closed in phase 10c-lite -- the filmographies, their TMDb
    biographies and their profile photos all ship. A preset still citing it
    would be citing work that is DONE, which is the same as citing nothing, so
    the four people packs now name the credit scan that actually blocks them."""
    assert [
        preset.key for preset in CATALOG if preset.gated_row == 83
    ] == []
```

- [ ] **Step 9: Run the paperwork tests**

```bash
docker compose -p p10ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py tests/test_collection_dynamic_types.py 2>&1 | tee /app/.superpowers/run-p10ct4-green1.log; echo EXIT=$?'
```

Expected: all pass. If `test_every_gated_row_cites_a_roadmap_row_that_exists`
fails, row 194's line does not match the table regex
(`^\|\s*(\d+)\s*\|`) — the row's leading pipe and spacing are wrong, not the
test.

- [ ] **Step 10: Mutation proof — the roadmap citation is really checked**

The whole point of `gated_row` is that it names a row somebody can read. Prove
the guard bites:

```bash
cp src/autoposter/collections/catalog.py /tmp/cat.bak
# edit: PERSON_SCAN_ROW = 999999
docker compose -p p10ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py -k cites_a_roadmap_row 2>&1 | tail -20'
cp /tmp/cat.bak src/autoposter/collections/catalog.py
cmp /tmp/cat.bak src/autoposter/collections/catalog.py && echo RESTORED-EXACT
docker compose -p p10ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_collection_catalog.py -k cites_a_roadmap_row 2>&1 | tail -5'
```

Paste both halves.

- [ ] **Step 11: Commit the paperwork**

Three commits, because they are three different kinds of change and a reviewer
should be able to reject one:

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): row 194 files the credit scan, row 83 closes, four citations corrected"

git add src/autoposter/collections/dynamic_types.py
git commit --no-gpg-sign -m "docs(collections): the people types get the per-type argument their neighbours have"

git add src/autoposter/collections/catalog.py tests/test_collection_catalog.py
git commit --no-gpg-sign -m "fix(catalog): the people packs name what actually blocks them, and the bio/poster half is built"
```

- [ ] **Step 12: The whole-branch gate**

```bash
docker compose -p p10ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'ruff check src tests 2>&1 | tee /app/.superpowers/run-p10ct4-ruff.log'
docker compose -p p10ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'pytest -q tests/test_builder_port_golden.py 2>&1 | tee /app/.superpowers/run-p10ct4-golden.log; echo EXIT=$?'
git diff --stat origin/main -- tests/fixtures/collections/golden_port.json
docker compose -p p10ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    up -d postgres
docker compose -p p10ct4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run -d --name p10ct4-full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-p10ct4-full.log; echo EXIT=$? >> /app/.superpowers/run-p10ct4-full.log'
docker wait p10ct4-full
tail -30 .superpowers/run-p10ct4-full.log
```

The `git diff --stat` against `golden_port.json` must print **nothing** — that
is global constraint 5, checked against the branch point rather than against the
working tree.

Also prove the phase touched nothing it said it would not:

```bash
git diff --stat origin/main -- src/autoposter/collections/builders/dynamic.py \
    src/autoposter/collections/dynamic_keys.py \
    src/autoposter/collections/dynamic_titles.py \
    src/autoposter/collections/engine.py \
    src/autoposter/collections/smart.py \
    src/autoposter/collections/filters.py \
    src/autoposter/collections/search_url.py \
    src/autoposter/config/schema.py
```

Expected: EMPTY. If anything appears, it is either a defect or a decision — say
which in the report.

- [ ] **Step 13: Write the PR body**

`.superpowers/sdd/p10c-pr-body.md`. No AI attribution (global constraint 8). It
carries:

1. **What shipped**, in operator words: person collections now carry the
   person's TMDb biography and profile photo. A definition's own `summary:`
   still wins; a poster file in the assets folder still wins; `collections.posters`
   still gates the whole poster step.
2. **The one behaviour change an existing deployment will SEE**, stated plainly
   and first among the risks: any existing `tmdb_actor`/`director`/`writer`/
   `producer`/`crew` collection without its own `summary:` gets a summary on the
   next pass, and one without a local poster file gets a poster. That includes
   the six `people_directors` collections if that preset is enabled. It is an
   upload per collection, once — `poster_sha256` means the pass after that
   uploads nothing.
3. **The cost:** one extra cached TMDb request per person definition per pass,
   at the client's TTL.
4. **What did not ship and where it went:** row 194 (the credit scan and the
   dynamic person types), row 160 (birthday/deathday), and Task 3's branch.
5. **The fence tests:** which one broke (`:421`, rewritten to pin the new
   behaviour), which one did not and why its docstring changed anyway (FLAG 1).
6. **The gates:** golden byte-identical (with the run), ruff clean, full suite
   green, every mutation proof's log path.
7. Any row-193 sighting seen during the phase.

- [ ] **Step 14: Teardown**

```bash
docker compose -p p10ct4 down
```

Never `down -v`.

---

## Self-Review

**1. Spec coverage.** Every adjudication maps to a task:

| Adjudication | Where |
| --- | --- |
| C1 SHIP: `tmdb_person` summaries/posters | T2 (whole task) |
| C1 SHIP: `tmdb_popular_people`, conditional on C3 | T3 branch A |
| C1 FILE: the credit-scan half, 192-grade | T4 Step 1 (row 194, ten required clauses) |
| C1: the four presets re-point from 83 to the new row | T4 Step 7 |
| C1: row 83 closes | T4 Step 2 |
| C2: upstream-read first | T1 (whole task) |
| C2: `/person/{id}` transport | T2 Steps 2–6 |
| C2: new poster SOURCE branch feeding `apply_poster`, not `hosted_poster_url` | T2 Steps 7–11 |
| C2: the two fence tests | T2 Steps 12–13 + **FLAG 1** |
| C2: hard-stop docstring updated in the same commit | T2 Step 16 (same commit as Step 15) |
| C3: T1 answers the actor-tag question before any code | T1 Step 7's three verdict lines; T3's decision gate |
| C3: honest filing if gated | T3 branch B |
| C4: birthday/deathday filed | T4 Step 3 (row 160, with the reasoning for choosing 160 over 194) |
| C5: preset descriptions corrected | T4 Step 7 (both the four gated and `people_directors`) |
| C6a: row 83's `Detail:` | T4 Step 2 |
| C6b: the 10c section's row-102 reference | T4 Step 4 |
| C6c: `dynamic_types.py:38-39` | T4 Step 6 |
| C6d (found while planning): line 902 | T4 Step 5 |
| C7: discipline inherited; no `_dynamic_rows()` loop, no checksum-over-record | Global Constraints; T4 Step 8 explicitly says the checksum does not move |
| C7: cut from `origin/main` after deferred-jobs merges | Branch and cut point; **FLAG 3** |
| C8: four tasks, T1 the upstream read | The task list |

**2. Placeholder scan.** The three shapes that look like placeholders and are
not: (a) Task 1's §-numbers, which are a fetch record's fixed structure, cited
by number from Tasks 2–4 — the same device `p10b-plan-draft.md` used; (b) Task
3's two branches, which are mutually exclusive by design and each fully
specified, with the tie-break named (B on any ambiguity); (c) the `# ...`
comments inside three of Task 2 Step 7's and Task 3's test bodies, which point
at an EXISTING double in the same file to reuse rather than describing work —
the assertions themselves are written out. Everything else carries its code.

**3. Type consistency.** `PersonDetail(biography, profile_path)` — two fields,
consistent between the transport (T2 Step 4), the test that pins the field set
(T2 Step 2) and `_profile`'s unpacking (T2 Step 15). `TMDB_PROFILE_KIND` is
defined once in `posters.py` (T2 Step 9), imported by `tmdb_person.py` (T2 Step
15) and by both test files. `tmdb_profile_url(profile_path: str) -> str | None`
has one spelling everywhere. `person_detail(person_id: int) -> PersonDetail` and
`popular_people(limit: int) -> list[PopularPerson]` match their call sites.
`PERSON_SCAN_ROW` replaces `PERSON_DYNAMIC_ROW` at both its definition and its
single read, with a grep check on either side.

**4. One thing a reviewer should push on.** Task 2 makes the bio/poster read
unconditional — no config knob. That is deliberate (YAGNI, and every existing
override already wins over it), but it is the phase's one operator-visible
behaviour change on an existing deployment, so the PR body states it first among
the risks rather than in a footnote.
