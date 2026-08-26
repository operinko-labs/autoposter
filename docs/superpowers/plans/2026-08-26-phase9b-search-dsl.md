# Phase 9b: The Plex Search DSL — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `plex_search` as a registered builder — Kometa's search vocabulary
translated into Plex query parameters (attributes, modifiers, and/or nesting,
sorts, limits), with a Kometa-string oracle proving the URLs byte-identical to
the ones Kometa 2.4.8 builds for the same configs.

**Architecture:** Five layers, each its own reviewer gate. (1) THE TABLE —
`FILTER_ATTRIBUTES` extended **in place** with search columns, so one row
carries both vocabularies and neither can drift from the other. (2) The sort
matrices and the limit, copied pre-encoded and verbatim. (3) A **pure**
URL builder over 9a's parsed predicate tree — no I/O, no plexapi — gated by
THE KOMETA-STRING ORACLE: golden URI strings for twelve hand-written configs,
produced by a standalone transcription of Kometa's own `build_filter` and
pinned as data. (4) The builder + params model + config surface + build-time
tag validation. (5) A read-only live probe against the operator's server,
sequenced last so everything before it merges without it.

**Tech Stack:** Python 3.14, pydantic v2, plexapi ≥4.16, pytest, ruff, Docker
Compose. No new runtime dependency except the one named decision in Task 4
Step 0 (`langcodes`), which a reviewer may decline.

---

## USER-VISIBLE NOTE — Task 5 needs the operator's Plex server

**Task 5 is the only task in this plan that touches a live server.** It is
read-only (queries and listings; no writes, no collection creation, no library
edits), and it is sequenced LAST deliberately: Tasks 1–4 are complete,
mergeable work on their own, and the branch can ship without Task 5 if the
server must stay quiet. Task 5 may need the operator's explicit go-ahead and a
window when the server is not busy. If that go-ahead does not come, Task 7
files the three open probes as roadmap rows and closes nothing that depends on
them — see Task 7 Step 4 for exactly which catalog rows stay GATED in that
case.

---

## Branch and cut point

- Branch name: **`feat/plex-search-dsl`**.
- **Cut point is decided at execution time.** The pending version-check PR
  (branch `fix/version-check-from-image-ref`) touches
  `src/autoposter/config/schema.py`, and so does this plan (Tasks 1 and 4).
  - If that PR has **merged**: cut `feat/plex-search-dsl` off `main` at the
    merge commit.
  - If it is **still pending**: cut off its tip
    (`fix/version-check-from-image-ref`), and write into the PR description:
    *"Stacked on #<vcheck PR>. Retarget this PR to `main` once that one
    merges."* Every stacked PR description carries the retarget instruction —
    the 9a precedent (its plan's Global Constraints, stack depth 3).
- Record the actual cut commit and the choice made in the Task 1 report.

## Execution

Executed via **superpowers:subagent-driven-development**: one fresh subagent
per task, two-stage review between tasks, this plan document **live-synced**
through every fix round (a fix that changes a signature, a message or a
checksum edits the corresponding step here in the same commit, so a later task's
implementer reads the truth and not the original guess).

---

## Global Constraints

Every task's requirements implicitly include this section.

**Testing is container-only.**
- A bare host `pytest` fails at collection by design (`tests/conftest.py`
  requires `AUTOPOSTER_TEST_DATABASE_URL` with no fallback). That is intended.
- Each task runs on its **own isolated compose project**, named `p9bt<N>`
  (`p9bt1`, `p9bt2`, `p9bt3`, `p9bt4`, `p9bt5`, `p9bt7`), so a sibling agent's
  run on the shared `-p autoposter` database is never contended:

  ```bash
  docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
      run --rm test sh -c 'timeout -s KILL 1800 pytest -q tests/test_collection_filters.py; echo EXIT=$?'
  ```

- **`timeout -s KILL N` inside `sh -c`, never a bare `timeout` as the container
  command.** BusyBox `timeout` execs in the parent, pytest becomes PID 1, and
  PID 1 ignores default SIGTERM — that produced 10-hour zombie containers
  squatting on the shared postgres.
- Teardown after each task: `docker compose -p p9bt<N> down`. **Never
  `down -v`** — that destroys the dev database volume. Never
  `docker compose down` on the shared `-p autoposter` project.
- **The repeated-foreground-docker-wait recipe**, for any run that outlives a
  single tool call's cap. Start the run detached with a name, then block in the
  FOREGROUND on `docker wait`, repeatedly, until it returns:

  ```bash
  # 1. start it, named, detached
  docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
      run --rm -d --name p9bt1-run test \
      sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
  # 2. block in the FOREGROUND (repeat this exact call until it prints an exit code)
  docker wait p9bt1-run
  # 3. read the result
  docker logs p9bt1-run | tail -40
  ```

  After starting ANY container run, the agent's next action MUST be one of
  (a) foreground `docker wait <name>`, (b) reading the finished container's
  logs, or (c) a different piece of work followed by (a)/(b). **Ending a turn
  while a container runs and expecting to be resumed is the failure**, whatever
  it is called — there is no notification mechanism. Nine recorded stalls; no
  phrasing grants an exception.
- If zombie `autoposter*run*` or `p9bt*` containers are found holding the DB,
  remove with `docker rm -f <id>` — never `docker compose down` on the shared
  project.

**Every task ends with golden + ruff, both green.**

```bash
docker compose -p p9bt<N> -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 1800 pytest -q tests/test_builder_port_golden.py tests/test_collection_catalog.py; echo EXIT=$?'
docker compose -p p9bt<N> -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
```

- **Golden gate byte-identical throughout.** `tests/test_builder_port_golden.py`
  reproduces `tests/fixtures/collections/golden_port.json` byte for byte, and
  **no default definition and no catalog preset gains a `plex_search`** in this
  phase. If the golden file changes, the change is a defect until proven
  otherwise; never re-capture it (re-capture is only ever legal on the
  pre-port commit).
- `ruff check src tests` — the lint select set is `["E4", "E7", "E9", "F"]`
  (pyproject.toml:73), pinned deliberately; do not broaden it.

**Paths.** Every file reference in a dispatch prompt, task brief, task report
or commit message uses the FULL repo-relative path (`src/autoposter/...`,
`tests/...`, `docs/...`, `.superpowers/...`). Shorthand is acceptable only
inside a sentence that already gave the full path.

**No timestamp-flake assertions (roadmap row 119).** Ten recorded instances of
wall-clock assumptions failing under shared-container load. Nothing in this
phase asserts on `datetime.now()`, elapsed time, or a database clock. Every
date-sensitive test pins its moment explicitly — `NOW = dt.datetime(2026, 8, 26,
14, 30, 0)` and pass it in — exactly as `tests/test_collection_filter_oracle.py`
already does. If a run of this phase hits a *pre-existing* instance of the
pattern, the task report records it for Task 7's row-119 tally and reruns; it
does not "fix" an unrelated test.

**Async-SQLAlchemy expired-attribute rule.** This phase adds **no database
surface at all** — no model, no migration, no query. Stated anyway because it
is a standing rule: after a commit, an ORM attribute is expired and touching it
issues a lazy load that raises under async; capture ids into locals BEFORE the
commit that expires them. If a task finds itself reaching for a session, that
is a sign the design drifted — stop and ask.

**Secrets and URL hygiene.**
- No probe output, no test fixture, no report, and no committed file carries
  the Plex server address, its token, or a `X-Plex-Token` query parameter. The
  Task 5 probe scrubs the host to `<plex-host>` before anything is written
  down, and scrubs `X-Plex-Token=...` to `X-Plex-Token=<redacted>`.
- **Class-name-only errors from Plex.** Any exception raised out of a plexapi
  call is caught and re-raised as this phase's own named exception carrying the
  exception's **class name and nothing else** — never `str(error)`, which can
  carry a tokenised URL. The engine's log line already prints the class name;
  `src/autoposter/collections/builders/plex_trivial.py:34-41` is the shape
  (`PlexLibraryUnavailable`), and `LibraryTypeMismatch`
  (`src/autoposter/collections/builders/base.py:237-243`) is the other.

**Fixtures and the oracle.**
- Kometa is pinned at **v2.4.8** (the same tag 9a's oracle used). Every
  transcribed table, list or branch carries `file:line` provenance in a comment
  — `modules/plex.py:595-601`, not "Kometa's searches list".
- The oracle driver imports **NOTHING** from this repository. Not `filters`,
  not `search_url`, not the test fixtures' loader. It may import stdlib. It
  does not need plexapi (unlike 9a's, which did, because Kometa's *filter* code
  reads plexapi objects; `build_filter` reads only the config dict).
- Oracle scripts live under `.superpowers/oracle/9b/` beside 9a's
  (`.superpowers/oracle/9a/kometa_oracle.py`, `ours.py`, `make_library.py`).

**Purity.**
- Nothing in config load, in expansion, or in any parse/URL function does I/O.
  `parse_filters` and `build_search_url` are pure; the tag resolver is
  **injected** as a callable, so tests and the oracle pass a dict and only the
  builder passes the Plex-backed one.
- The one exception, argued: build-time tag validation (D4) makes Plex calls,
  in `build`, cached per `(library, field, libtype)` in `ctx.run_cache` — the
  same place and the same reasoning as `require_library_type`
  (`src/autoposter/collections/builders/base.py:246-269`): a definition with no
  `libraries:` key applies to every library in the pass, so the library is only
  known at build time.

**Conventions.**
- `git commit --no-gpg-sign`. **No `Co-Authored-By` and no AI attribution** in
  any commit message or PR description.
- Stage files **by name**. Never `git add -A`, `git add .`, `git commit -a`.
- **Never restore a file with `git checkout -- <file>`.** Copy it aside first
  and restore from the copy, verified with `cmp`. A `git checkout --` wiped
  another session's uncommitted work in a previous phase.
- `.gitattributes` forces LF. If editing with a Python script use
  `write_bytes` or `newline="\n"`; `write_text` emits CRLF on Windows and turns
  a small edit into a whole-file diff.
- Use PowerShell for docker commands; Git Bash mangles container paths.

**The bar for tests.** This repository has shipped tests that could not fail
six times. Every guard test must be proven falsifiable: break the thing it
guards, show it red, restore (from the copy, verified with `cmp`), show it
green, and paste **both real outputs**. Never present output from one run as
evidence for another — that happened, and review caught it by noticing two
"different" runs had byte-identical output.

**Pre-authorized known-pattern fixes.** These may be applied without a new
adjudication, and each MUST be reported and MUST be synced into this plan
document in the same commit:
- capture-ids-before-expiry (the async-SQLAlchemy rule above);
- replacing a wall-clock assertion with a pinned moment (row 119);
- widening a refusal message's wording without changing what it refuses;
- correcting a transcribed table cell **when the oracle or the fetched Kometa
  file disagrees with this plan** — Kometa's source wins over this document,
  every time, and the divergence is recorded on the row it fixes (the 9a
  `SETTLED-BY-ORACLE` marker convention).

---

## Verified facts (re-derived 2026-08-26 against Kometa v2.4.8; spot-check before relying)

Everything in this section was computed or read directly, not recalled. Where
it says *derivation*, the command is given so a reviewer can rerun it.

**Kometa's search vocabulary** (`modules/plex.py:594-601`, the `searches`
comprehension over `boolean_attributes` :509-526, `string_attributes` :507 ×
`string_modifiers` :508, `tag_attributes` :552-591 + `year_attributes` :546 ×
`tag_modifiers` :592 minus `no_not_mods` :593, `date_attributes` :528-544 ×
`date_modifiers` :545, `number_attributes` :547 × `number_modifiers` :548,
`float_attributes` :549 × `float_modifiers` :550 minus `duration.rated`):

- **342** total combinations, **155** music, **187 non-music**, over **55
  distinct non-music attributes**. Confirmed by rerunning the comprehension
  standalone.
- `movie_only_searches` (:430-445) = **14** entries. `show_only_searches`
  (:446-506) = **59** entries.
- `searches` contains **4 duplicate** strings, because `studio` and `edition`
  are in **both** `string_attributes` and `tag_attributes`. That is not
  cosmetic: in `validate_attribute` the `.regex` branch (`builder.py:4301`)
  is tested **before** the string branch (`:4326`), so `studio.regex` in a
  *search* resolves against the library's tag vocabulary and emits resolved
  KEYS — a client-side vocabulary expansion wearing a regex's clothes. This is
  D5's evidence, from the source.

**Kometa's filter vocabulary** (`modules/builder.py:270-350`, `filters_by_type`):
**70** distinct names. Ours (9a's `FILTER_ATTRIBUTES`) covers exactly **15** of
them, so the residue is **55** — not "~45", which is what
`src/autoposter/collections/catalog.py:1341` still says (Task 7 fixes it).

**The two vocabularies are NOT nested** (derivation: intersect the 70 filter
names with the 55 search attributes):
- **26** names are in both.
- **44** are filter-only, with no Plex search field at all: `aspect`,
  `height`, `width`, `channels`, `versions`, `filepath`, `folder`, `summary`,
  `record_label`, `season_title`, `show_title`, `audio_track_title`,
  `subtitle_track_title`, `video_codec`, `video_profile`, `audio_profile`,
  `has_collection`, `has_edition`, `has_overlay`, `has_dolby_vision`,
  `has_stinger`, `stinger_rating`, `history`, `seasons`, `episodes`, `albums`,
  `tracks`, `first_episode_aired`, `last_episode_aired`,
  `last_episode_aired_or_never`, `origin_country`, `original_language`,
  `imdb_keyword`, `tmdb_keyword`, `tmdb_genre`, `tmdb_title`, `tmdb_status`,
  `tmdb_type`, `tmdb_year`, `tmdb_vote_count`, `tmdb_vote_average`,
  `tvdb_genre`, `tvdb_status`, `tvdb_title`.
- **29** are search-only, with no client-side filter: `decade`, `dovi`, `hdr`,
  `trash`, `duplicate`, `unmatched`, `unplayed`, `progress`,
  `unplayed_episodes`, `show_unmatched`, `folder_location`,
  `season_collection`, `season_label`, and the sixteen `episode_*` names.
- **`plays` and `last_played` are in BOTH vocabularies** — they are Kometa
  filters as well as searches (`builder.py:280-293`). See the CONTRADICTIONS
  section below.

**`modifier_translation`** (`modules/plex.py:195`), verbatim:

```
"" -> ""      ".not" -> "!"      ".is" -> "%3D"    ".isnot" -> "!%3D"
".gt" -> "%3E%3E"   ".gte" -> "%3E"   ".lt" -> "%3C%3C"   ".lte" -> "%3C"
".before" -> "%3C%3C"   ".after" -> "%3E%3E"
".begins" -> "%3C"      ".ends" -> "%3E"
".regex" -> ""          ".rated" -> ""
```

It is **not a bijection**, and there are **four** collision pairs, every one of
them cross-type — which is precisely why the translation table must be keyed
`(value_type, operator)` the way `PLEXAPI_EQUIVALENT` already is:

| wire string | reached from |
| --- | --- |
| `%3E` | `.gte` (int/float/duration) **and** `.ends` (str) |
| `%3C` | `.lte` (int/float/duration) **and** `.begins` (str) |
| `%3E%3E` | `.gt` (int/float/duration) **and** `.after` (date) |
| `%3C%3C` | `.lt` (int/float/duration) **and** `.before` (date) |

**`build_filter`** (`modules/builder.py:4092-4295`) — the URL assembly, read
line by line:
- sort type key and default sort from `plex.sort_types` (`plex.py:779-787`):
  `movie -> (default "title.asc", key 1, movie_sorts)`,
  `show -> ("title.asc", 2, show_sorts)`.
- `sort_by` (:4130-4141) — **`sort_by`, never `sort`**; a list is accepted;
  an unknown name raises naming the options; when absent the type default is
  used, so **every** built URL carries a `sort=`.
- `limit` (:4144-4158) — an int ≥ 1, or the literal `all` (which sets no
  limit); emitted as `limit=N&` before `sort=`.
- `validate` (:4160-4167) — a bool that downgrades every per-attribute error
  from a raise to a log line. **Refused here (D1).**
- The implicit base (:4261-4276) — with neither `any:` nor `all:` at the top,
  Kometa splits the keys into `and_searches` (`plex.py:391-407`, the `.and`
  suffixes) which go into an ANDed base dict, and `or_searches`
  (`plex.py:408-429`) which each become their own one-key `any` block.
  **Refused here (D1).**
- `_filter` (:4169-4259) — `conjunction = "and=1&"` for an `all` block,
  `"or=1&"` for `any`; each term is `f"{arg_key}{mod}={arg}&"` (:4191); terms
  after the first are prefixed by the conjunction (:4252).
- Nesting (:4207-4217) — `util.get_list(_data)` makes a mapping a
  one-element list; **each element is rendered with `is_all = (attr == "all")`**
  — i.e. the *written* key's conjunction — and wrapped `push=1&…pop=1&`; the
  elements are joined by the **containing** block's conjunction, not by the
  written key's. See the NESTING DIVERGENCE note below.
- Final assembly (:4287-4289):
  `?type={key}&{limit=N&}sort={'%2C'.join(...)}&{body[:-1] if base_all else f"push=1&{body}pop=1"}`.
- The URL is used as `section.fetchItems(f"/library/sections/{key}/all{url}")`
  (`plex.py:958-959`, called at `:1879-1883`). **`includeCollections` appears
  nowhere in that path** — it is the result-set footgun 9a's probe hit
  (roadmap Notes-for-9b item 1), and a test asserts no built URL carries it.

**Per-type special cases in `_filter`**, in the order Kometa tests them:
| Condition | Emitted | Line |
| --- | --- | --- |
| date attr, modifier `""`/`.not` | `{field}{'%3E%3E' if bare else '%3C%3C'}=-{N}{unit}&`, `o` rewritten to `mon` | :4222-4233 |
| `duration` with `.gt/.gte/.lt/.lte` | `{field}{mod}={minutes * 60000}&` | :4234-4235 |
| `.rated` | `{field}{'!' if true else ''}=-1&` | :4236-4237 |
| boolean attr | `{field}{'' if true else '!'}=1&` | :4238-4241 |
| tag/string/year attr with `""/.is/.isnot/.not/.begins/.ends/.regex` | one term per written value, `quote()`d for string attrs, the resolved KEY for tag attrs, joined by the **block's** conjunction | :4242-4248 |
| anything else | `{field}{mod}={value}&` | :4249-4250 |

**Value validation** (`modules/builder.py:4297-4456`), the branches v1 needs:
- string attrs (`:4326-4327`): the written values, unsplit, unresolved.
- year attrs (`:4328-4349`): ints; `current_year[-N]` words handled — **not
  v1**, filed for Task 6.
- tag attrs with `""`/`.not` (`:4400-4440`): resolved through
  `get_search_choices` (`plex.py:1300-1316`), whose lookup is keyed on **four**
  strings per choice — `title`, `key`, `title.lower()`, `key.lower()` — and
  which raises `Plex Error: {attribute}: {value} not found` when a written
  value is in none of them. For a `plex_search` the stored value is the
  **key**, never the title (`title=not plex_search`).
- `audio_language`/`subtitle_language` in a search (`:4411-4419`) bypass
  `get_search_choices` entirely for `get_language_search_values`
  (`plex.py:1321-1344`): if the written code is itself a value Plex reports,
  only that exact value is targeted; otherwise **every** library value whose
  base ISO 639-1 code matches is returned — and each becomes its own URL term,
  joined by the block's conjunction. Under an `all` block that is an AND across
  the variants, which is Task 5's probe #2.
- date attrs `.before`/`.after` (`:4441-4445`): `validate_date` →
  `"%Y-%m-%d"`; the literal `today` becomes `datetime.now()`.
- date attrs `""`/`.not` in a search (`:4446-4452`): if the value's last
  character is one of `s m h d w o y` it is the unit and the rest is the count;
  otherwise the unit is `d`. Result is the string `f"{count}{unit}"`.
- float attrs (`:4453-4454`): `util.parse(datatype="float")`, which is
  `float(str(value))` (`util.py:861`) — so **`duration.gt: 90` becomes
  `90.0`, and `90.0 * 60000` renders in the URL as `5400000.0`, with the
  trailing `.0`.** Likewise `critic_rating.gte: 8` renders as `8.0`. The oracle
  pins this; do not "tidy" it.
- `quote` is `urllib.parse.quote(str(data))` with the default `safe="/"`
  (`modules/request.py:37-38`).

**`Plex.split`** (`modules/plex.py:2735-2751`): lowercases the key, applies
`method_alias` (:234-305) and `modifier_alias` (:306), and rewrites `.gt`/`.gte`
to `.after` and `.lt`/`.lte` to `.before` on every date attribute. 9a already
refuses those four on dates for exactly this reason
(`src/autoposter/collections/filters.py:137-151`).

**The sort matrices** (`modules/plex.py:604-636` movie, `:637-667` show):
**31** movie entries (15 name pairs + `random`), **29** show entries (14 pairs
+ `random`). Both are stored **pre-encoded** (`%3Adesc`, `%2C`) and must be
copied, never retyped. `sort_types` (:779-787) carries the `type=` numbers:
movie 1, show 2, season 3, episode 4, artist 8, album 9, track 10.

**The 9a foundation, as it stands today:**
- `src/autoposter/collections/filters.py` — `VALUE_TYPES` (:112),
  `ITEM_KINDS` (:113), `SOURCE_TIERS` (:122), `OPERATORS_BY_TYPE` (:130-151),
  `DEFAULT_OPERATOR` (:156-163), `PLEXAPI_EQUIVALENT` (:180-213), `_NEGATES`
  (:220), `_MISSING_ALWAYS_EXCLUDES` (:227), `FilterAttribute` (:230-258),
  `FILTER_ATTRIBUTES` (:284-456, fifteen rows), `BY_NAME` (:458), `ItemView`
  (:461-482), `FilterPredicate` (:485-499), `FilterGroup` (:502-508), the
  coercions `_as_text`/`_as_regex`/`_as_int`/`_as_float`/`_as_minutes`/
  `_as_date`/`_as_days` (:524-710), `_parse_value` (:713-727), `_split_key`
  (:730-763), `_parse_predicate` (:766-772), `_parse_block` (:775-790),
  `_parse_nested` (:793-810), `parse_filters` (:813-822), `predicates`
  (:825-838), and the evaluation half (:844-1050).
- `src/autoposter/collections/filter_values.py` — `_LISTING_ATTRIBS` (:87-95),
  `SHIPPED_ATTRIBUTES`/`DEFERRED_ATTRIBUTES` (:173-178), `PlexItemView`
  (:181-196), `AttributeNotInListing` (:74-79).
- `src/autoposter/config/schema.py` — `CollectionDefinition` (:264-544):
  `params` (:278), `libraries` (:282), `sort` (:285, the *collection's* Plex
  order, distinct from params' `sort_by`), `limit` (:292), `filters` (:343);
  `_must_be_a_registered_builder` (:345-360), the params-model validator
  (:362-414), `_membership_knobs_need_a_membership` (:416-459), and
  `_filters_must_parse_and_be_readable` (:486-544) — whose refusal copy Task 1
  edits.
- `src/autoposter/collections/builders/base.py` — the `Builder` protocol
  (:186-197), `REGISTRY`/`register` (:219-234), `LibraryTypeMismatch`
  (:237-243), `require_library_type` (:246-269), and `PlexIdParams`/
  `PlexIdBuilder` (:299-328), the worked example a params model follows.
- `src/autoposter/collections/builders/sources_bundle.py` — `PlexSectionAccess`
  (:49-77): `section()` for traversals an index cannot answer, `owned_index()`
  for the engine's lazy shared index.
- `src/autoposter/collections/builders/plex_trivial.py` — the model for a
  builder whose source *is* the library, including `PlexLibraryUnavailable`
  (:34-41) and the zero-request assertion (:70-73).
- `src/autoposter/collections/engine.py` — `_INHERITED_BY_EXPANSION`
  (:155-169), and the pass: build → `resolve_external` (:449) → the filter
  stage (:457-474) → `limit` (:476-481) → preview (:484-490).
- `src/autoposter/collections/builders/__init__.py` — registration, all in one
  file, `register(...)` at :76-134.

### NESTING DIVERGENCE — the one place `filters:` and `plex_search` disagree

9a's `_parse_nested` (`src/autoposter/collections/filters.py:793-810`) makes
each element of a **list** an `all` block, so `filters: {any: [{a, b}, {c}]}`
means `(a AND b) OR c`. That is right for `filters:`, which is Kometa's
`Builder.check_filters` (`builder.py:4674-4754`), and 9a's oracle settled it.

`build_filter` does something else (`builder.py:4214`): each element is
rendered with `is_all = (attr == "all")` — the **written key's** conjunction —
and the elements are joined by the **containing block's** conjunction. So
`plex_search: {all: {..., any: [{a, b}, {c}]}}` means
`push(a OR b) AND push(c)`.

One grammar, two renderings. Task 1 handles it with a single parameter rather
than a second parser or a refusal: `parse_filters(..., searching=True)` gives a
list's elements the **written** op and marks the wrapper `inline=True`, so the
URL builder splices the elements into the parent's stream instead of wrapping
them in one extra `push`/`pop`. Boolean evaluation is unaffected (AND and OR
are associative), so `evaluate` needs no change. The oracle's configs 3 and 4
pin both shapes.

### CONTRADICTIONS between the adjudications and upstream

Recorded, not resolved — Task 1's implementer reads these before touching the
table, and the fix-round adjudicates any that bite.

1. **The fact sheet's named modifier collisions are wrong.**
   `.superpowers/sdd/p9b-facts.md` says ".gte/.begins both `>`; .gt/.before
   both `>>`". Neither is true: `.begins` is `%3C` (colliding with `.lte`) and
   `.before` is `%3C%3C` (colliding with `.lt`). The *mechanism* the fact sheet
   mandates — key the table `(type, operator)`, assert the collisions
   structurally — stands unchanged; only the two example pairs are misstated,
   and there are four pairs, not two. This plan uses the four verified pairs.
2. **`plays` and `last_played` are not search-only.** The fact sheet lists
   "`plays/last_played/unplayed/progress/hdr/decade/episode_*`" as search-only.
   `unplayed`, `progress`, `hdr`, `decade` and the `episode_*` family are;
   `plays` and `last_played` are in Kometa's *filter* vocabulary too
   (`builder.py:280-293`). Task 1 therefore gives them `filterable=True` — and
   a new source tier, because 9a never probed whether `viewCount`/
   `lastViewedAt` reach the section listing, and claiming a probe verdict we do
   not have would be worse than the gap.
3. **"the 8 dual-resident rows" is nine.** All nine of 9a's shipped filter
   attributes are also search attributes. Task 5 round-trips nine and its
   report says which, if any, could not be round-tripped and why (the likely
   candidate is `duration`, whose bare form v1 refuses — see 4).
4. **`duration` ships `.gt/.gte/.lt/.lte` only.** ~~Kometa's `duration` and
   `duration.not` are legal searches for both libtypes and are *not* passed
   through the ×60000 conversion, so `duration: 90` means ninety
   milliseconds.~~ **RETRACTED by Task 1's live fetch — see the TASK 1
   SPEC-SYNC block below.** `duration` is a `float_attribute` (`plex.py:549`)
   and `searches` gives `float_attributes` only `float_modifiers`
   (`plex.py:600`), so a bare `duration:` is not in `plex.searches` at all and
   `Builder._filter` refuses it outright (`builder.py:4194-4195`). The
   conclusion (four range modifiers, bare form refused) is unchanged; the
   *reason* is that Kometa has no such search, not that it means milliseconds.
   For the ranges that do exist the two blocks agree on the unit — Kometa
   multiplies by 60000 (`builder.py:4234`) exactly as the client-side view
   divides by it.
5. **`langcodes` is not a dependency of this repo.** The fact sheet requires
   `get_language_search_values`' base-ISO expansion "transcribed, tested", and
   Kometa implements it with `langcodes` (`plex.py:141-151`). Transcribing the
   ~184-row ISO 639-2→639-1 map by hand is precisely the fallible-transcription
   failure mode this phase exists to prevent. Task 4 Step 0 is an explicit
   decision point with three options and a default.

---

## File Structure

| Path | Responsibility | Task |
| --- | --- | --- |
| `src/autoposter/collections/filters.py` | **Modified in place.** The one table gains search columns; `SEARCH_MODIFIERS`, `SEARCH_OPERATORS_BY_TYPE`, `SEARCH_OPERATORS_EXCLUDED`, `RelativeWindow`; `parse_filters(..., searching=)` and the vocabulary-gate refusals. | T1 |
| `src/autoposter/config/schema.py` | **Modified.** The `filters:` refusal copy branches on the new source tiers (T1); `plex_search` needs no schema field — its shape is its params model (T4). | T1, T4 |
| `src/autoposter/collections/search_sorts.py` | **New.** `MOVIE_SORTS`, `SHOW_SORTS`, `SORT_TYPES`, `sort_argument`, `known_sort_names`, `require_sort_for_libtype`. | T2 |
| `src/autoposter/collections/search_url.py` | **New.** `build_search_url` and the per-type term renderers. Pure; no plexapi, no I/O. | T3 |
| `src/autoposter/collections/builders/plex_search.py` | **New.** `PlexSearchParams`, `PlexSearchBuilder`, the cached Plex-backed tag resolver, the named exceptions. | T4 |
| `src/autoposter/collections/builders/__init__.py` | **Modified.** One import, one `register(...)`. | T4 |
| `tests/test_collection_filters.py` | Extended: the new columns, the checksums, the modifier-collision structural test, the search-vocabulary refusals. | T1 |
| `tests/test_collection_search_sorts.py` | **New.** The sort tables' checksums and the weird rows. | T2 |
| `tests/test_collection_search_url.py` | **New.** Per-type term rendering, nesting, the `includeCollections` guard. | T3 |
| `tests/test_collection_search_oracle.py` | **New.** THE KOMETA-STRING ORACLE: twelve configs, twelve golden URI strings pinned as data. | T3 |
| `tests/test_builder_plex_search.py` | **New.** The builder, the params model, the refusals, the tag resolution, the run-cache. | T4 |
| `tests/test_collection_config.py` | Extended: load-time refusals through `CollectionDefinition`. | T4 |
| `.superpowers/oracle/9b/kometa_build_filter.py` | **New, not shipped.** Kometa's `build_filter` + `validate_attribute`, transcribed standalone. Imports nothing from this repo. | T3 |
| `.superpowers/oracle/9b/ours.py` | **New, not shipped.** Our side of the same twelve configs. | T3 |
| `.superpowers/sdd/p9b-task-5-probe.md` | **New, not shipped.** The live probe's script and scrubbed results. | T5 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modified.** Rows 96, 101, 154, 157, 158 + new rows. | T7 |
| `src/autoposter/collections/catalog.py` | **Modified.** `media_aspect`'s copy fix; any row T5 unblocks. | T7 |

---

## Task 1: The extended table

> **TASK 1 SPEC-SYNC (applied 2026-08-26, commits `2b70175` + `c4537d9`).**
> Five corrections. Four came from verifying every Kometa-derived value in this
> section against a LIVE fetch of v2.4.8
> (`raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8/modules/{plex,builder}.py`)
> rather than from the plan's transcription; the fifth is a test that could not
> pass as written. All are applied inline below and marked `SPEC-SYNC`.
>
> 1. **`SEARCH_OPERATORS_BY_TYPE["float"]` loses `eq` and `not`.** `searches`
>    gives `float_attributes` only `float_modifiers` — the four ranges plus
>    `.rated` (`plex.py:549-550`, `:600`). Neither `critic_rating:` nor
>    `critic_rating.not:` is in `plex.searches`, and `Builder._filter` checks a
>    written key against exactly that list (`builder.py:4194-4195`), so Kometa
>    answers both with "attribute is not valid". The plan would have shipped
>    two keys Plex is never asked.
> 2. **`plays` gains `SEARCH_OPERATORS_EXCLUDED["plays"] = ("eq", "not")`.**
>    `plays` is a `number_attribute` and not a `year_attribute`
>    (`plex.py:547`, `:599`), so it takes `number_modifiers` alone. `year` —
>    which is both — keeps the bare form and `.not`, which is why the type-level
>    `int` set is unchanged and the subtraction is per-row.
> 3. **The bare-`duration` millisecond claim is retracted** (Notes-for-9b item
>    4, the `SEARCH_OPERATORS_BY_TYPE` comment, `_split_key`'s message and the
>    test that asserted it). The conclusion holds — four range modifiers, bare
>    form refused — but the reason is that Kometa has no such search, not that
>    it means milliseconds. The ranges that do exist are minutes on both sides
>    (`builder.py:4234`).
> 4. **`test_the_modifier_table_is_not_invertible`'s collision computation was
>    unsatisfiable.** *(Resolution APPROVED by the controller, 2026-08-26: the
>    narrowed-and-strengthened test ships as committed.)*
>    `len(keys) > 1` over `(type, operator)` pairs collects
>    `!` (one operator, three types) and both date-window entries, so the
>    assertion failed against the very table the same section specifies:
>    `{'%3C%3C': ['before','lt','not']} != {'%3C%3C': ['before','lt']}`,
>    `{'%3E%3E': ['after','eq','gt']} != {'%3E%3E': ['after','gt']}`, plus
>    `Left contains 1 more item: {'!': ['not']}`. The four pairs the plan
>    re-derived are correct and unchanged; the computation now excludes the two
>    date-window entries by name and counts distinct OPERATORS, and asserts the
>    full pair sets so flattening the key still fails. Mutation-proved.
> 5. **A fourth 9a checksum needed updating**, beyond the three this section
>    names: `test_every_operator_has_a_case_set_including_a_missing_value`
>    builds its expected pairs from `OPERATORS_BY_TYPE`, so adding `bool` broke
>    it. `bool` is excluded there with the reason spelled out (search-only, no
>    filterable row, unreachable from `evaluate`).
>
> **Every `python` block in this Task 1 section is now byte-identical to the
> shipped files** (audited mechanically; the only remaining difference is this
> section's own deliberate `"...existing note, unchanged..."` elisions in the
> fifteen-rows block). Copy from these blocks, not from memory. Three pieces of
> shipped prose have no block here, because this section never specified their
> text — they are pointed at rather than duplicated, so no second copy can
> drift:
>
> - the **set-arithmetic record** (T7's honesty artifact) — the module
>   docstring of `src/autoposter/collections/filters.py`, the section beginning
>   "**The set arithmetic (phase 9b)**";
> - the table's **header comment**, restated for nineteen rows — same file,
>   beginning "Nineteen rows: 9a's fifteen";
> - the **tier-branching note** on `_filters_must_parse_and_be_readable` —
>   `src/autoposter/config/schema.py`, beginning "The refusal copy branches on
>   the source".
>
> Confirmed exactly as written: the 70/55/26/44/29 set arithmetic, `date_sub_mods`,
> `modifier_translation` and its four collisions, `show_translation`'s three
> episode-libtype rescopings, `no_not_mods`, `movie_only_searches`,
> `show_only_searches`, every `search_field` value, and every `builder.py` line
> citation in this section (`:4194`, `:4207-4217`, `:4224`, `:4234`,
> `:4236-4241`, `:4278-4284`, `:4301`, `:4326`, `:4443`, `:4446-4452`).

**Files:**
- Modify: `src/autoposter/collections/filters.py` (the table's columns, the
  search vocabulary tables, the parser's `searching=` mode)
- Modify: `src/autoposter/config/schema.py:486-544`
  (`_filters_must_parse_and_be_readable` — the refusal copy branches on the two
  new source tiers)
- Test: `tests/test_collection_filters.py` (extend)

**Interfaces (later tasks rely on these exactly):**
- `FilterAttribute` gains stored columns `search_field: str | None`,
  `show_search_field: str | None`, `search_kinds: tuple[str, ...]`,
  `filterable: bool`; and derived properties `searchable -> bool`,
  `search_operators -> tuple[str, ...]`.
- `SEARCH_MODIFIERS: dict[tuple[str, str], str]` — `(value_type, operator)` →
  the URL-encoded modifier.
- `SEARCH_OPERATORS_BY_TYPE: dict[str, tuple[str, ...]]` and
  `SEARCH_OPERATORS_EXCLUDED: dict[str, tuple[str, ...]]`.
- `RelativeWindow(count: int, unit: str)` — a frozen dataclass; `unit` is one
  of `s m h d w o y`.
- `parse_filters(raw, *, field="filters", searching=False, base="all") -> FilterGroup`.
- `FilterGroup` gains `inline: bool = False`.
- `SEARCHABLE_ATTRIBUTES: tuple[str, ...]` and
  `FILTERABLE_ATTRIBUTES: tuple[str, ...]`, both derived from the table in
  table order.

### Step 0: Record the baseline

- [ ] **Step 0**

```bash
docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9bt1-base test \
    sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9bt1-base
docker logs p9bt1-base | tail -5
```

Expected: a line of the form `NNNN passed, 0 skipped` (the imagemagick tests
skip on a non-HDRI build; if they do, record BOTH counts). Write the exact
numbers and the cut commit into the task report. Every later "full suite"
expectation in this plan is *that* number plus the tests the task added.

### Step 1: Write the failing test for the new columns and their checksums

- [ ] **Step 1**

**~~Three~~ FOUR of 9a's own tests are checksums over the table and MUST be
updated here** — they are not collateral damage, they are the thing being
re-checked. Do not add a parallel copy beside them; edit them in place, keeping
their docstrings and appending to them.

**SPEC-SYNC (Task 1): there is a fourth**, item 4 below, which this section
originally missed. `test_every_operator_has_a_case_set_including_a_missing_value`
(`:736-747` in 9a) builds its expected pair set from `OPERATORS_BY_TYPE`, so
adding `"bool": ("eq",)` breaks it — and a case-set for `bool` cannot be
written at all, because every `bool` row is `filterable=False` and
`_split_key` refuses one in a `filters:` block before `evaluate` can see it.

In `tests/test_collection_filters.py`:

1. `test_the_table_holds_exactly_the_tier_one_rows` (`:66-88`) — append the
   four new names to the expected list, in table order, and extend the
   docstring:

```python
def test_the_table_holds_exactly_the_tier_one_rows():
    """The row list is the plan's, in the plan's order (roadmap.md:538-551), and
    the count is the transcription's checksum: a row lost, duplicated or renamed
    in an edit shows up here rather than as a filter an operator writes and
    nothing applies. Pinning the order too means the table stays readable
    against the roadmap it came from rather than drifting into edit order.

    Phase 9b appended four, at the end rather than interleaved, so the fifteen
    above still read against the roadmap line they came from: ``plays`` and
    ``last_played`` are in BOTH of Kometa's vocabularies and were never probed
    for the client-side one, and ``unplayed`` and ``progress`` are search-only.
    """
    assert [row.name for row in FILTER_ATTRIBUTES] == [
        "genre",
        "year",
        "resolution",
        "audience_rating",
        "critic_rating",
        "content_rating",
        "audio_language",
        "subtitle_language",
        "label",
        "added",
        "release",
        "duration",
        "studio",
        "network",
        "collection",
        "plays",
        "last_played",
        "unplayed",
        "progress",
    ]
```

2. `test_the_column_totals_are_the_transcriptions_checksum` (`:104-144`) — the
   `by_type` dict gains `"bool"` (it is built over `VALUE_TYPES`, so the new
   type appears as a key whether or not the assertion expects it), `int`
   becomes 2, `date` becomes 3, and the two new source tiers get their own
   lists:

```python
    assert {k: len(v) for k, v in by_type.items()} == {
        "tag": 8,
        "str": 1,
        "int": 2,
        "float": 2,
        "date": 3,
        "duration": 1,
        "bool": 2,
    }
    assert by_source["listing"] == [
        "year",
        "resolution",
        "audience_rating",
        "critic_rating",
        "content_rating",
        "added",
        "release",
        "duration",
        "studio",
    ]
    assert by_source["tier2-deferred"] == [
        "genre",
        "audio_language",
        "subtitle_language",
        "label",
        "network",
        "collection",
    ]
    assert by_source["probe"] == []
    # 9b's two, and they are two tiers rather than one because the REASONS
    # differ: ``unprobed`` means Kometa filters on it and 9a never asked
    # whether the listing carries it; ``search-only`` means Kometa has no
    # filter of that name at all, so there is nothing to ask.
    assert by_source["unprobed"] == ["plays", "last_played"]
    assert by_source["search-only"] == ["unplayed", "progress"]
```

3. `test_item_kinds_are_movie_show_or_both` (`:146-152`) — `unplayed` and
   `progress` join the movie-only list, and the both-kinds count goes 11 → 13:

```python
    assert movie_only == [
        "audio_language", "progress", "resolution", "subtitle_language", "unplayed",
    ]
    assert show_only == ["network"]
    assert len([r for r in FILTER_ATTRIBUTES if r.kinds == ("movie", "show")]) == 13
```

4. **SPEC-SYNC (Task 1), the one this section originally missed.**
   `test_every_operator_has_a_case_set_including_a_missing_value` — exclude
   `bool` from the expected pairs, with the reason in the docstring, and add
   an assertion so that a future FILTERABLE boolean row has to delete the
   exclusion rather than inherit it silently. Shipped verbatim:

```python
def test_every_operator_has_a_case_set_including_a_missing_value():
    """The structural half of the claim "every operator is table-driven".

    A ``(type, operator)`` pair with no case-set fails here rather than
    quietly shipping untested, and so does a case-set that forgot the
    missing-value rule -- which is a table-level invariant every operator has
    to honour, not a per-operator detail someone may reasonably skip.

    ``bool`` is excluded, and it is the one exclusion this test will accept: it
    is a SEARCH-ONLY type (phase 9b), every ``bool`` row is
    ``filterable=False``, and ``_split_key`` therefore refuses one in a
    ``filters:`` block before ``evaluate`` can ever see it. Its entry in
    ``OPERATORS_BY_TYPE`` exists only to keep ``FilterAttribute.operators``
    total over ``VALUE_TYPES``; a case-set for it could not be written as a
    config key at all. A future FILTERABLE boolean row would have to delete
    this exclusion, which is the point of spelling it out rather than
    filtering on ``OPERATOR_CASES``.
    """
    pairs = {
        (t, op) for t, ops in OPERATORS_BY_TYPE.items() for op in ops if t != "bool"
    }

    assert set(OPERATOR_CASES) == pairs
    assert all(row.filterable is False for row in FILTER_ATTRIBUTES if row.type == "bool")
```

Then **append** the genuinely new cases (note that
`test_every_row_is_typed_sourced_and_scoped_from_the_fixed_vocabularies`
(`:91-101`) already asserts every row has a note, so 9b adds no second one):

```python
# --- the search half of the table (phase 9b Task 1) ---------------------------


def test_the_search_kinds_column_is_its_own_and_differs_from_kinds():
    """Two kind columns, because they genuinely differ.

    ``resolution`` is movie-only as a CLIENT filter (a show's resolution is a
    property of its episodes, and per-episode traversal is not a tier-1 read)
    and both-kinds as a SEARCH -- Plex answers it at the episode libtype, and
    the server does the traversal for free. ``duration`` goes the other way:
    both-kinds client-side, movie-only as a search, because Kometa's
    ``movie_only_searches`` lists its four range modifiers (plex.py:441-444).
    """
    from collections import Counter

    from autoposter.collections.filters import BY_NAME, FILTER_ATTRIBUTES

    assert Counter(row.search_kinds for row in FILTER_ATTRIBUTES) == {
        ("movie", "show"): 15, ("movie",): 3, ("show",): 1,
    }
    assert BY_NAME["resolution"].kinds == ("movie",)
    assert BY_NAME["resolution"].search_kinds == ("movie", "show")
    assert BY_NAME["duration"].kinds == ("movie", "show")
    assert BY_NAME["duration"].search_kinds == ("movie",)


def test_every_row_is_searchable_and_seventeen_are_filterable():
    """The set arithmetic, pinned so it cannot rot silently.

    Kometa's search vocabulary is 55 non-music attributes and its filter
    vocabulary is 70 names; this table covers 19 of the first and 17 of the
    second. The module docstring carries the full derivation.
    """
    from autoposter.collections.filters import (
        FILTERABLE_ATTRIBUTES,
        FILTER_ATTRIBUTES,
        SEARCHABLE_ATTRIBUTES,
    )

    assert all(row.searchable for row in FILTER_ATTRIBUTES)
    assert len(SEARCHABLE_ATTRIBUTES) == 19
    assert len(FILTERABLE_ATTRIBUTES) == 17
    assert set(SEARCHABLE_ATTRIBUTES) - set(FILTERABLE_ATTRIBUTES) == {
        "unplayed", "progress",
    }


def test_the_show_search_field_rescoping_is_transcribed():
    """``show_translation`` (Kometa modules/plex.py:168-193) re-scopes a search
    field for a show library, and three of them go to the EPISODE libtype
    rather than the show's -- which is the whole reason the column exists."""
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["genre"].show_search_field == "show.genre"
    assert BY_NAME["added"].show_search_field == "show.addedAt"
    assert BY_NAME["resolution"].show_search_field == "episode.resolution"
    assert BY_NAME["audio_language"].show_search_field == "episode.audioLanguage"
    assert BY_NAME["subtitle_language"].show_search_field == "episode.subtitleLanguage"
    # network is already show-scoped by search_translation, so show_translation
    # never sees it -- the two columns are equal, not None.
    assert BY_NAME["network"].search_field == "show.network"
    assert BY_NAME["network"].show_search_field == "show.network"


def test_field_for_picks_the_libtypes_field_and_refuses_a_libtype_it_does_not_serve():
    """``field_for`` raises rather than falling back, and that is the whole
    design: a caller asking for a field on a libtype the row does not serve has
    already skipped the ``search_kinds`` check, and answering with the movie
    field would build a query Plex silently answers with the WRONG SET rather
    than with an error. ``resolution`` is the row that shows why the fallback
    would be wrong even when it "works" -- a show library must be asked at the
    episode libtype."""
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["resolution"].field_for("movie") == "resolution"
    assert BY_NAME["resolution"].field_for("show") == "episode.resolution"
    # No show_search_field means the movie field serves both, not that the row
    # is unanswerable -- ``critic_rating`` has one, so check a row that does
    # not: ``network`` is show-only and its two columns are equal.
    assert BY_NAME["network"].field_for("show") == "show.network"

    with pytest.raises(ValueError, match="not searchable on a show library"):
        BY_NAME["duration"].field_for("show")
    with pytest.raises(ValueError, match="not searchable on a movie library"):
        BY_NAME["network"].field_for("movie")


def test_every_row_searchable_on_show_carries_a_show_search_field():
    """Pins ``field_for``'s show-library safety (line 498-499) against a future
    row: a row with ``"show" in search_kinds`` and ``show_search_field=None``
    would silently return the MOVIE field for a show library, since
    ``field_for`` only rescopes when ``show_search_field`` is set. It holds for
    all nineteen rows today; nothing but this test pins it."""
    for row in FILTER_ATTRIBUTES:
        if "show" in row.search_kinds:
            assert row.show_search_field is not None, row.name

```

- [ ] **Step 2: Run it to see it fail**

```bash
docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_filters.py; echo EXIT=$?'
```

Expected: the whole file errors at collection with
`ImportError: cannot import name 'SEARCHABLE_ATTRIBUTES' from
'autoposter.collections.filters'` (the new tests import it at module scope
through the shared import block at `:32-53`, so collection fails before any
test runs). If the import is deferred instead, expect the three edited
checksums plus the three new cases red — six failures — and no others.

- [ ] **Step 3: Extend the vocabularies and the dataclass**

In `src/autoposter/collections/filters.py`, replace the `VALUE_TYPES` /
`SOURCE_TIERS` block (:112-122) with:

```python
# The four categorical columns, as closed sets. A row outside them would parse,
# load, and match nothing.
#
# ``bool`` joined in 9b and is SEARCH-ONLY in practice: Kometa's boolean
# attributes (``unplayed``, ``progress``, ``hdr``, ...) are answered by the
# Plex server as ``field=1`` / ``field!=1`` (builder.py:4238-4241) and have no
# client-side counterpart in Kometa's filter vocabulary at all.
VALUE_TYPES = ("tag", "str", "int", "float", "date", "duration", "bool")
ITEM_KINDS = ("movie", "show")

# Where a row's data comes from, for the CLIENT-SIDE filter. ``probe`` was Task
# 2's question, not a shrug: Plex's listing endpoint carries some child elements
# and not others depending on the server and the request, so a row marked
# ``probe`` becomes ``listing`` (ships scan-free) or ``tier2-deferred`` (drops
# out of tier 1 rather than shipping a silent request-per-item) once the
# read-only probe answers. Task 2's probe (2026-08-25) answered all seven of
# tier 1's, so no row carries ``probe`` today. 9b added two tiers rather than
# reusing an existing one, because both would have been a lie:
#
# - ``unprobed``: the attribute IS in Kometa's filter vocabulary, but 9a never
#   probed whether the section listing carries it, so there is no verdict to
#   cite. Refusing it in ``filters:`` with the tier2-deferred copy would claim
#   a probe finding that does not exist. ``plays`` and ``last_played`` are the
#   two: Kometa filters on both (builder.py:280-293) and searches on both.
# - ``search-only``: the attribute is not in Kometa's filter vocabulary at all,
#   so there is nothing to probe. ``unplayed`` and ``progress``.
SOURCE_TIERS = ("listing", "probe", "tier2-deferred", "unprobed", "search-only")
```

Add after `DEFAULT_OPERATOR` (:163):

```python
# --- the SEARCH half of the vocabulary (phase 9b) ----------------------------
#
# Distinct from ``OPERATORS_BY_TYPE`` above and deliberately so: an attribute
# can be legal in one block and refused in the other, and one table with two
# operator sets is the only shape in which the two cannot drift apart. Every
# entry is transcribed from Kometa v2.4.8's ``searches`` comprehension
# (modules/plex.py:594-601), which is also the gate ``Builder._filter`` checks
# a written key against (builder.py:4194) -- a name.modifier absent from it is
# refused by Kometa outright, not merely undocumented. The differences from the
# client-side set are each argued below.
SEARCH_OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    # ``.regex`` is deliberately absent (see SEARCH_ONLY_OPERATORS' sibling
    # note below and ``_split_key``): Kometa's search-regex is not a regex sent
    # to Plex, it is a client-side expansion over the library's tag vocabulary
    # (builder.py:4301-4323), so shipping the spelling here would make one
    # config key mean two mechanisms.
    "tag": ("eq", "not"),
    "str": ("contains", "not", "is", "isnot", "begins", "ends"),
    # The bare form and ``.not`` are here for ``year`` alone, which reaches
    # them by being a ``year_attribute`` and therefore taking ``tag_modifiers``
    # as well as ``number_modifiers`` (plex.py:597, :599). The table's other
    # ``int`` row, ``plays``, is a ``number_attribute`` only, and
    # SEARCH_OPERATORS_EXCLUDED subtracts the two from it.
    "int": ("eq", "not", "gt", "gte", "lt", "lte"),
    # NOT ``eq``/``not``, and this is a TRANSCRIPTION CORRECTION made against a
    # live fetch of v2.4.8 rather than from the plan's text. Kometa's
    # ``float_attributes`` take ``float_modifiers`` and nothing else
    # (plex.py:549-550, :600), which is the four ranges plus ``.rated``: there
    # is no ``critic_rating:`` and no ``critic_rating.not:`` in ``searches`` at
    # all, so Kometa answers either with "attribute is not valid"
    # (builder.py:4194-4195). Offering them here would have shipped two keys
    # Plex is never asked.
    #
    # ``.rated`` is search-only: Plex answers "has any rating at all" as
    # ``field!=-1`` (builder.py:4236-4237), which no client-side comparison
    # spells.
    "float": ("gt", "gte", "lt", "lte", "rated"),
    # The four ranges only, for the same reason as ``float`` -- ``duration`` IS
    # a ``float_attribute`` (plex.py:549), with ``.rated`` subtracted from it
    # by name (plex.py:600). So a bare ``duration:`` is not a Kometa search
    # either; ``_split_key`` refuses it saying so. Where the two blocks DO
    # agree is the unit: Kometa multiplies a search duration by 60000
    # (builder.py:4234) exactly as the client-side view divides by it, so
    # ``duration.gt: 90`` is ninety minutes in both.
    "duration": ("gt", "gte", "lt", "lte"),
    "date": ("eq", "not", "before", "after"),
    "bool": ("eq",),
}

# Per-row subtractions from the type's search operator set. ``resolution`` is
# transcribed from ``no_not_mods`` (modules/plex.py:593) -- Plex has no negated
# resolution filter. ``plays`` is the ``int`` row that is not a year: see the
# ``int`` note above (plex.py:547-548, :599).
SEARCH_OPERATORS_EXCLUDED: dict[str, tuple[str, ...]] = {
    "resolution": ("not",),
    "plays": ("eq", "not"),
}

# Operators that exist ONLY in a search, so that writing one in a ``filters:``
# block is answered by name rather than by the generic "does not apply" line.
SEARCH_ONLY_OPERATORS = ("rated",)

# The modifier translation, keyed ``(value_type, operator)``.
#
# Kometa's own ``modifier_translation`` (modules/plex.py:195) is keyed on the
# modifier string alone, and it is NOT a bijection: four wire strings are each
# reached from two different modifiers, and every one of those collisions is
# between two DIFFERENT value types --
#
#     %3E    .gte (int/float/duration)  and  .ends   (str)
#     %3C    .lte (int/float/duration)  and  .begins (str)
#     %3E%3E .gt  (int/float/duration)  and  .after  (date)
#     %3C%3C .lt  (int/float/duration)  and  .before (date)
#
# -- so the pair key is not decoration, it is the only key under which the
# table is a function. ``test_the_modifier_table_is_not_invertible`` pins that
# structurally, so nobody "simplifies" this into a one-level dict.
#
# The two date entries below are the odd ones: a bare or ``.not`` date in a
# search is a relative window, and Kometa takes its modifier from
# ``last_mod`` (builder.py:4224) rather than from ``modifier_translation`` --
# ``%3E%3E`` for "in the last N", ``%3C%3C`` for "not in the last N". They are
# written here so the renderer has one lookup and not two, which is also why
# they are NOT part of the four collisions above: they do not come from
# ``modifier_translation`` at all.
#
# ``("float", "rated")`` and ``("bool", "eq")`` are the empty string because
# for those two the NEGATION rides on the argument, not on the modifier: a
# ``.rated`` term is ``field!=-1`` or ``field=-1``, and a boolean term is
# ``field=1`` or ``field!=1`` (builder.py:4236-4241). The renderer supplies the
# ``!``; this table must not, or it would be applied twice.
SEARCH_MODIFIERS: dict[tuple[str, str], str] = {
    ("tag", "eq"): "",
    ("tag", "not"): "!",
    ("str", "contains"): "",
    ("str", "not"): "!",
    ("str", "is"): "%3D",
    ("str", "isnot"): "!%3D",
    ("str", "begins"): "%3C",
    ("str", "ends"): "%3E",
    ("int", "eq"): "",
    ("int", "not"): "!",
    ("int", "gt"): "%3E%3E",
    ("int", "gte"): "%3E",
    ("int", "lt"): "%3C%3C",
    ("int", "lte"): "%3C",
    ("float", "gt"): "%3E%3E",
    ("float", "gte"): "%3E",
    ("float", "lt"): "%3C%3C",
    ("float", "lte"): "%3C",
    ("float", "rated"): "",
    ("duration", "gt"): "%3E%3E",
    ("duration", "gte"): "%3E",
    ("duration", "lt"): "%3C%3C",
    ("duration", "lte"): "%3C",
    ("date", "eq"): "%3E%3E",
    ("date", "not"): "%3C%3C",
    ("date", "before"): "%3C%3C",
    ("date", "after"): "%3E%3E",
    ("bool", "eq"): "",
}

# Kometa's ``date_sub_mods`` (modules/plex.py:307), which is both the legal
# unit set for a relative window and the name each unit reads as. ``o`` is
# MONTHS and ``m`` is MINUTES -- the least guessable pair in the vocabulary,
# and the reason ``_as_window``'s refusal spells the whole table out.
RELATIVE_UNITS: dict[str, str] = {
    "s": "Seconds", "m": "Minutes", "h": "Hours", "d": "Days",
    "w": "Weeks", "o": "Months", "y": "Years",
}
```

Add `("bool", "eq"): "exact"` to `PLEXAPI_EQUIVALENT` (after the `duration`
block, before the two date `None`s) with this comment:

```python
    # A boolean has no client-side operator set (it is search-only), so this
    # entry exists to keep the table total over OPERATORS_BY_TYPE rather than
    # because anything reads it. ``exact`` is the honest key: plexapi would
    # compare the value for equality.
    ("bool", "eq"): "exact",
```

Add `"eq"` for bool to `DEFAULT_OPERATOR`:

```python
    "bool": "eq",
```

Add to `OPERATORS_BY_TYPE`, so the type is total there too:

```python
    # Search-only in practice -- every ``bool`` row is ``filterable=False`` --
    # but the type has to have an entry or ``FilterAttribute.operators`` raises
    # a KeyError for a row nothing was ever going to evaluate.
    "bool": ("eq",),
```

Replace the `FilterAttribute` dataclass (:230-258) with:

```python
@dataclass(frozen=True)
class FilterAttribute:
    """One row of the table -- both vocabularies.

    ``name`` is Kometa's own name for the attribute and is what an operator
    writes in YAML; where it differs from Plex's wire name (``release`` for
    ``originallyAvailableAt``, ``critic_rating`` for ``rating``) the row's note
    says so, because that difference is the reason the row exists rather than a
    passthrough. ``note`` is required by the table's own test: a row with
    nothing to say about itself is a row nobody checked.

    **The client-side half.** ``kinds`` is which library the attribute means
    anything for as a ``filters:`` predicate; ``source`` is one of
    ``SOURCE_TIERS``; ``filterable`` is whether the attribute is in Kometa's
    FILTER vocabulary at all (modules/builder.py:278-350). The two are
    independent: ``plays`` is filterable and unprobed, ``unplayed`` is neither.

    **The search half.** ``search_field`` is the Plex query field for a MOVIE
    library, after ``search_translation`` (modules/plex.py:60-138);
    ``show_search_field`` is the same after ``show_translation``
    (modules/plex.py:168-193) re-scopes it for a SHOW library -- which for
    three media attributes means the EPISODE libtype, not the show's.
    ``search_kinds`` is which library types Plex will answer the search for,
    transcribed from ``movie_only_searches`` (:430-445) and
    ``show_only_searches`` (:446-506), and it is a SEPARATE column from
    ``kinds`` because they genuinely differ -- ``resolution`` is movie-only
    client-side and both-kinds server-side.
    """

    name: str
    type: str
    kinds: tuple[str, ...]
    source: str
    note: str
    search_field: str | None
    show_search_field: str | None
    search_kinds: tuple[str, ...]
    filterable: bool

    @property
    def operators(self) -> tuple[str, ...]:
        """Derived, not stored. The operators are a property of the value type,
        so storing them per row would be a duplicate of ``OPERATORS_BY_TYPE``
        and the duplicate is the thing that rots."""
        return OPERATORS_BY_TYPE[self.type]

    @property
    def default_operator(self) -> str:
        return DEFAULT_OPERATOR[self.type]

    @property
    def searchable(self) -> bool:
        """Derived from ``search_field``, not stored beside it -- a row with a
        field and ``searchable=False`` would be a contradiction the table could
        hold."""
        return self.search_field is not None

    @property
    def search_operators(self) -> tuple[str, ...]:
        """The type's search operators, minus this row's own subtractions."""
        excluded = SEARCH_OPERATORS_EXCLUDED.get(self.name, ())
        return tuple(
            op for op in SEARCH_OPERATORS_BY_TYPE[self.type] if op not in excluded
        )

    def field_for(self, libtype: str) -> str:
        """The Plex query field for one library type.

        Raises rather than falling back: a caller asking for a field on a
        libtype the row does not serve has already skipped the
        ``search_kinds`` check, and answering with the movie field would build
        a query Plex silently answers with the wrong set.
        """
        if self.search_field is None:
            raise ValueError(f"{self.name!r} has no Plex search field")
        if libtype not in self.search_kinds:
            raise ValueError(
                f"{self.name!r} is not searchable on a {libtype} library"
            )
        if libtype == "show" and self.show_search_field is not None:
            return self.show_search_field
        return self.search_field
```

- [ ] **Step 4: Fill in the fifteen existing rows' new columns**

Every existing row in `FILTER_ATTRIBUTES` gains four arguments. Written as
keywords so a reader sees which is which, and so a future column cannot be
mis-positioned. Only the added lines are shown; **do not touch the existing
`note` text** except where a step below says to.

```python
    FilterAttribute(
        "genre", "tag", _BOTH, "tier2-deferred",
        "...existing note, unchanged...",
        search_field="genre", show_search_field="show.genre",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "year", "int", _BOTH, "listing",
        "...existing note, unchanged...",
        search_field="year", show_search_field="show.year",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "resolution", "tag", ("movie",), "listing",
        "...existing note, unchanged...",
        # Movie-only as a client filter and BOTH as a search: Plex answers a
        # show library's resolution at the EPISODE libtype
        # (show_translation, plex.py:186), which is exactly the per-episode
        # traversal the client-side accessor refuses to pay for -- the server
        # does it for free.
        search_field="resolution", show_search_field="episode.resolution",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "audience_rating", "float", _BOTH, "listing",
        "...existing note, unchanged...",
        search_field="audienceRating", show_search_field="show.audienceRating",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "critic_rating", "float", _BOTH, "listing",
        "...existing note, unchanged...",
        search_field="rating", show_search_field="show.rating",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "content_rating", "tag", _BOTH, "listing",
        "...existing note, unchanged...",
        search_field="contentRating", show_search_field="show.contentRating",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "audio_language", "tag", ("movie",), "tier2-deferred",
        "...existing note, unchanged...",
        search_field="audioLanguage", show_search_field="episode.audioLanguage",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "subtitle_language", "tag", ("movie",), "tier2-deferred",
        "...existing note, unchanged...",
        search_field="subtitleLanguage",
        show_search_field="episode.subtitleLanguage",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "label", "tag", _BOTH, "tier2-deferred",
        "...existing note, unchanged...",
        search_field="label", show_search_field="show.label",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "added", "date", _BOTH, "listing",
        "...existing note, unchanged...",
        search_field="addedAt", show_search_field="show.addedAt",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "release", "date", _BOTH, "listing",
        "...existing note, unchanged...",
        search_field="originallyAvailableAt",
        show_search_field="show.originallyAvailableAt",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "duration", "duration", _BOTH, "listing",
        "...existing note, unchanged...",
        # MOVIE-ONLY as a search, and only for the four range modifiers:
        # ``duration.gt``/``.gte``/``.lt``/``.lte`` are in
        # movie_only_searches (plex.py:441-444). There is no bare ``duration``
        # search at all -- see SEARCH_OPERATORS_BY_TYPE's note -- and the
        # ranges that do exist are MINUTES on both sides, because Kometa
        # multiplies a search duration by 60000 (builder.py:4234).
        search_field="duration", show_search_field=None,
        search_kinds=("movie",), filterable=True,
    ),
    FilterAttribute(
        "studio", "str", _BOTH, "listing",
        "...existing note, unchanged...",
        # ``studio`` is in BOTH of Kometa's search category lists -- it is a
        # string_attribute (plex.py:507) AND a tag_attribute (plex.py:568),
        # which is where two of the duplicate entries in ``searches`` come
        # from. Which branch wins depends on the modifier:
        # ``validate_attribute`` tests ``.regex`` against the TAG list first
        # (builder.py:4301) and only then the string list (builder.py:4326).
        # Since ``.regex`` is refused here (see SEARCH_OPERATORS_BY_TYPE),
        # every operator this table ships takes the STRING branch, and the
        # value goes to Plex ``quote()``d and unresolved.
        search_field="studio", show_search_field="show.studio",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "network", "tag", ("show",), "tier2-deferred",
        "...existing note, unchanged...",
        # Already show-scoped by search_translation (plex.py:63), so
        # show_translation never sees it and the two columns are equal rather
        # than the second being None. 9a proved the ITEM attribute absent on
        # Plex 1.43.4; whether the SEARCH field answers is Task 5's probe #1,
        # the single highest-value question this phase asks.
        search_field="show.network", show_search_field="show.network",
        search_kinds=("show",), filterable=True,
    ),
    FilterAttribute(
        "collection", "tag", _BOTH, "tier2-deferred",
        "...existing note, unchanged...",
        search_field="collection", show_search_field="show.collection",
        search_kinds=_BOTH, filterable=True,
    ),
```

- [ ] **Step 5: Add the four new rows**

Append inside `FILTER_ATTRIBUTES`, after the `collection` row:

```python
    # --- rows 9b added ------------------------------------------------------
    #
    # None of the four is a client-side filter today, and the two REASONS are
    # different, which is why ``SOURCE_TIERS`` grew two values rather than one.
    FilterAttribute(
        "plays", "int", _BOTH, "unprobed",
        "Plex's `viewCount` -- how many times the item has been played by the "
        "account the token belongs to. In BOTH of Kometa's vocabularies: a "
        "search (plex.py:80, :547) and a filter (builder.py:280-293). Its "
        "source tier is `unprobed`, not `tier2-deferred`, and the distinction "
        "is deliberate: 9a's probe never asked whether `viewCount` reaches the "
        "section listing, so there is no verdict to cite and the "
        "tier2-deferred refusal copy -- which cites one -- would be a claim "
        "nobody checked. A `filters:` block naming it refuses saying exactly "
        "that. Note also that `viewCount` is PER-ACCOUNT: the answer depends "
        "on whose token the pass runs with, which is a property no other row "
        "in this table has. As a SEARCH it takes the four range modifiers and "
        "nothing else -- it is a number_attribute and not a year_attribute "
        "(plex.py:547, :599) -- so SEARCH_OPERATORS_EXCLUDED subtracts the "
        "bare form and `.not` that its `int` type otherwise offers.",
        search_field="viewCount", show_search_field="show.viewCount",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "last_played", "date", _BOTH, "unprobed",
        "Plex's `lastViewedAt`. In both vocabularies, like `plays`, and "
        "`unprobed` for the same reason. Per-account, like `plays`. As a "
        "search its bare and `.not` forms are RELATIVE WINDOWS -- "
        "`last_played.not: 6o` is \"not played in the last six months\", the "
        "shape a stale-media collection wants -- and roadmap row 154's "
        "timezone divergence applies to it in the opposite direction from "
        "`added`: a server-side date predicate evaluates in the PLEX SERVER's "
        "clock, not the runner's.",
        search_field="lastViewedAt", show_search_field="show.lastViewedAt",
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "unplayed", "bool", ("movie",), "search-only",
        "Plex's `unwatched` (plex.py:84), a server-side boolean: `unplayed: "
        "true` emits `unwatched=1`, `false` emits `unwatched!=1` "
        "(builder.py:4238-4241). MOVIE-ONLY -- it is in movie_only_searches "
        "(plex.py:439). A show library's equivalent is `unplayed_episodes` "
        "(`show.unwatchedLeaves`), which is a different attribute with a "
        "different meaning (how many episodes, not whether the show) and is "
        "filed for the per-family tail rather than aliased silently. "
        "`search-only`: Kometa has no `unplayed` FILTER at all, so `filters:` "
        "refuses it by naming the block it does belong to. Per-account.",
        search_field="unwatched", show_search_field=None,
        search_kinds=("movie",), filterable=False,
    ),
    FilterAttribute(
        "progress", "bool", ("movie",), "search-only",
        "Plex's `inProgress` (plex.py:89): partially played. Movie-only "
        "(plex.py:440); the show equivalent is `episode_progress`, filed for "
        "the tail. `search-only` like `unplayed`, and per-account like it. "
        "Note that Kometa's `progress` SORT (`viewOffset`, plex.py:623-624) "
        "is a different thing under the same word -- a sort key, not a "
        "predicate -- and the sort table carries it independently.",
        search_field="inProgress", show_search_field=None,
        search_kinds=("movie",), filterable=False,
    ),
```

- [ ] **Step 6: Add the derived views**

After `BY_NAME` (:458):

```python
# Derived from the table, in table order, so each reads as a subset of it
# rather than as an independent list. ``filter_values.SHIPPED_ATTRIBUTES``
# already does the same thing one column along.
SEARCHABLE_ATTRIBUTES: tuple[str, ...] = tuple(
    row.name for row in FILTER_ATTRIBUTES if row.searchable
)
FILTERABLE_ATTRIBUTES: tuple[str, ...] = tuple(
    row.name for row in FILTER_ATTRIBUTES if row.filterable
)
```

Add all seven new names to `__all__` (:92-108), keeping it sorted:
`"FILTERABLE_ATTRIBUTES"`, `"RELATIVE_UNITS"`, `"RelativeWindow"`,
`"SEARCHABLE_ATTRIBUTES"`, `"SEARCH_MODIFIERS"`,
`"SEARCH_ONLY_OPERATORS"`, `"SEARCH_OPERATORS_BY_TYPE"`,
`"SEARCH_OPERATORS_EXCLUDED"`.

- [ ] **Step 7: Run the whole filters test file to green**

```bash
docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_filters.py; echo EXIT=$?'
```

Expected: green — 9a's cases (three of them edited above) plus the three new
ones. Nothing from 9a should need any change beyond those three checksums; if
something else went red, the new columns changed behaviour they should not
have.

- [ ] **Step 8: Commit**

```bash
git add src/autoposter/collections/filters.py tests/test_collection_filters.py
git commit --no-gpg-sign -m "feat(filters): the search columns on the one attribute table

FILTER_ATTRIBUTES gains search_field, show_search_field, search_kinds and
filterable, and four rows (plays, last_played, unplayed, progress). Two new
source tiers -- unprobed and search-only -- because 'tier2-deferred' cites a
probe verdict that does not exist for these.

Transcribed from Kometa v2.4.8 modules/plex.py:60-193, :430-506, :594-601."
```

### The modifier-collision structural test

- [ ] **Step 9: Write the failing test**

Append to `tests/test_collection_filters.py`:

```python
def test_the_modifier_table_is_not_invertible():
    """Why ``SEARCH_MODIFIERS`` is keyed on a PAIR.

    Kometa's own ``modifier_translation`` (modules/plex.py:195) maps four wire
    strings from two different modifiers each, and every collision is between
    two DIFFERENT value types -- so a one-level dict keyed on the modifier
    cannot represent the table without picking a winner. This test fails the
    moment somebody "simplifies" the key.

    Two exclusions, and both are about what the claim above actually is rather
    than about making the numbers work:

    - the two date-WINDOW entries are not ``modifier_translation`` entries at
      all. Kometa takes their wire string from ``last_mod``
      (builder.py:4224), and ``SEARCH_MODIFIERS`` carries them only so the
      renderer has one lookup instead of two. They are excluded by name;
    - a collision is a wire reached by more than one distinct OPERATOR. That
      is precisely what makes a modifier-keyed dict lossy. ``!`` is reached
      from three of our pairs -- ``(tag, not)``, ``(str, not)``, ``(int,
      not)`` -- but from ONE modifier, ``.not``, so Kometa stores it once and
      means one thing by it, and it is not a collision.
    """
    from collections import defaultdict

    from autoposter.collections.filters import SEARCH_MODIFIERS

    from_modifier_translation = {
        key: wire
        for key, wire in SEARCH_MODIFIERS.items()
        if key not in (("date", "eq"), ("date", "not"))
    }
    reached_by = defaultdict(set)
    for (value_type, operator), wire in from_modifier_translation.items():
        reached_by[wire].add((value_type, operator))

    collisions = {
        wire: keys
        for wire, keys in reached_by.items()
        if wire != "" and len({operator for _, operator in keys}) > 1
    }
    # The four pairs, by operator. The types differ within every pair, which is
    # the load-bearing half.
    assert {wire: sorted({op for _, op in keys}) for wire, keys in collisions.items()} == {
        "%3E": ["ends", "gte"],
        "%3C": ["begins", "lte"],
        "%3E%3E": ["after", "gt"],
        "%3C%3C": ["before", "lt"],
    }
    # And the pairs in full, so that flattening the key to the operator alone
    # cannot leave this test green by accident.
    assert collisions["%3E"] == {
        ("str", "ends"), ("int", "gte"), ("float", "gte"), ("duration", "gte"),
    }
    assert collisions["%3C"] == {
        ("str", "begins"), ("int", "lte"), ("float", "lte"), ("duration", "lte"),
    }
    assert collisions["%3E%3E"] == {
        ("date", "after"), ("int", "gt"), ("float", "gt"), ("duration", "gt"),
    }
    assert collisions["%3C%3C"] == {
        ("date", "before"), ("int", "lt"), ("float", "lt"), ("duration", "lt"),
    }
    for wire, keys in collisions.items():
        assert len({value_type for value_type, _ in keys}) > 1, wire


def test_the_modifier_table_is_total_over_the_search_operators():
    """Every (type, operator) an attribute can actually be written with has a
    wire string. A missing entry would be a KeyError at URL-build time, on a
    config that loaded clean."""
    from autoposter.collections.filters import (
        FILTER_ATTRIBUTES,
        SEARCH_MODIFIERS,
    )

    for row in FILTER_ATTRIBUTES:
        for operator in row.search_operators:
            assert (row.type, operator) in SEARCH_MODIFIERS, (row.name, operator)


def test_resolution_has_no_negated_search():
    """``no_not_mods`` (modules/plex.py:593). Plex will not answer a negated
    resolution filter, so the table must not offer one."""
    from autoposter.collections.filters import BY_NAME

    assert "not" not in BY_NAME["resolution"].search_operators
    assert "not" in BY_NAME["genre"].search_operators


def test_duration_ships_only_its_range_operators_as_a_search():
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["duration"].search_operators == ("gt", "gte", "lt", "lte")
    assert BY_NAME["duration"].operators == ("eq", "not", "gt", "gte", "lt", "lte")


def test_a_rating_and_a_play_count_search_take_their_ranges_only():
    """Task 1's transcription CORRECTION, pinned so it cannot drift back.

    The plan's text gave ``float`` the bare form and ``.not`` as searches. A
    live fetch of Kometa v2.4.8 says otherwise: ``float_attributes`` take
    ``float_modifiers`` and nothing else (plex.py:549-550, :600), so neither
    ``critic_rating:`` nor ``critic_rating.not:`` is in ``plex.searches`` --
    and ``Builder._filter`` checks a written key against exactly that list
    (builder.py:4194-4195), so Kometa answers both with "attribute is not
    valid". ``plays`` is the same shape one type along: it is a
    ``number_attribute`` and NOT a ``year_attribute`` (plex.py:547, :599), so
    it gets ``number_modifiers`` alone, while ``year`` -- which is both --
    keeps the bare form and ``.not``.

    The CLIENT-side operator sets are untouched by any of this, which is the
    whole point of the two columns.
    """
    from autoposter.collections.filters import BY_NAME

    assert BY_NAME["critic_rating"].search_operators == ("gt", "gte", "lt", "lte", "rated")
    assert BY_NAME["audience_rating"].search_operators == ("gt", "gte", "lt", "lte", "rated")
    assert BY_NAME["plays"].search_operators == ("gt", "gte", "lt", "lte")
    assert BY_NAME["year"].search_operators == ("eq", "not", "gt", "gte", "lt", "lte")

    assert BY_NAME["critic_rating"].operators == ("eq", "not", "gt", "gte", "lt", "lte")
    assert BY_NAME["plays"].operators == ("eq", "not", "gt", "gte", "lt", "lte")

    for written in ({"critic_rating": 8}, {"plays": 3}):
        with pytest.raises(ValueError, match="is not a plex_search"):
```

- [ ] **Step 10: Run, expect failures, then green**

```bash
docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_filters.py -k "modifier_table or resolution_has_no or duration_ships"; echo EXIT=$?'
```

Expected after Step 3's tables are in place: `4 passed`. If any fails, the
table is wrong, not the test — Kometa's file is the authority.

- [ ] **Step 11: Prove the collision test falsifiable**

```bash
cp src/autoposter/collections/filters.py /tmp/filters.py.bak
```

Edit `SEARCH_MODIFIERS`: change `("str", "ends"): "%3E"` to `("str", "ends"): "%3E%3F"`.
Rerun the command from Step 10. Expected: `1 failed` —
`test_the_modifier_table_is_not_invertible`, with `%3E` no longer in
`collisions`. Then:

```bash
cp /tmp/filters.py.bak src/autoposter/collections/filters.py
cmp /tmp/filters.py.bak src/autoposter/collections/filters.py && echo RESTORED
```

Expected: `RESTORED`. Rerun Step 10: `4 passed`. **Paste both real outputs in
the task report.**

### The parser's search mode

- [ ] **Step 12: Write the failing tests for `searching=True`**

Append to `tests/test_collection_filters.py`.

**SPEC-SYNC (Task 1):** this block originally opened with its own
`import datetime as dt` / `import pytest` /
`from autoposter.collections.filters import RelativeWindow, parse_filters`.
It must not: `dt`, `pytest`, `parse_filters` and `FilterPredicate` are already
imported at the top of the file, and a second module-level import block
part-way down fails `ruff` (E402). As shipped, `RelativeWindow` is added to the
existing import block at `:32-46` and nothing else changes.

```python
def _only(group):
    """The single predicate in a one-key block."""
    (child,) = group.children
    return child


def test_a_bare_date_in_a_search_is_a_relative_window():
    predicate = _only(parse_filters({"added": 30}, searching=True))
    assert predicate.values == (RelativeWindow(30, "d"),)


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        (30, RelativeWindow(30, "d")),
        ("30", RelativeWindow(30, "d")),
        ("30d", RelativeWindow(30, "d")),
        ("6o", RelativeWindow(6, "o")),
        ("2y", RelativeWindow(2, "y")),
        ("90m", RelativeWindow(90, "m")),
        ("12h", RelativeWindow(12, "h")),
        ("4w", RelativeWindow(4, "w")),
        ("45s", RelativeWindow(45, "s")),
    ],
)
def test_every_relative_window_unit_parses(written, expected):
    predicate = _only(parse_filters({"last_played.not": written}, searching=True))
    assert predicate.values == (expected,)


def test_a_bare_date_in_a_filter_is_still_a_day_count():
    """The client-side grammar is unchanged: ``added: 30`` is an int, and the
    unit suffixes are refused, because ``filters:`` evaluates in python and has
    no server to hand ``30d`` to."""
    predicate = _only(parse_filters({"added": 30}))
    assert predicate.values == (30,)
    with pytest.raises(ValueError, match="not a number of days"):
        parse_filters({"added": "30d"})


def test_a_relative_window_refuses_an_unknown_unit_naming_all_seven():
    with pytest.raises(ValueError) as error:
        parse_filters({"added": "30x"}, searching=True)
    message = str(error.value)
    assert "filters.added" in message
    assert "o = months" in message
    assert "m = minutes" in message


def test_an_attribute_no_row_names_is_refused_with_the_right_vocabulary():
    """Two vocabularies, two lists. ``aspect`` is one of the 44 Kometa filter
    names with no Plex search field, and no row names it yet, so both blocks
    answer "unknown" -- but each names ITS OWN vocabulary, not the table."""
    with pytest.raises(ValueError) as error:
        parse_filters({"aspect": "1.78"}, searching=True)
    message = str(error.value)
    assert "aspect" in message
    assert "plex_search" in message
    assert "unplayed" in message        # a searchable name is offered
    assert "plays" in message

    with pytest.raises(ValueError) as error:
        parse_filters({"aspect": "1.78"})
    message = str(error.value)
    assert "filters:" in message
    assert "unplayed" not in message    # search-only names are NOT offered


def test_a_search_refuses_a_filter_only_attribute_naming_the_other_block(monkeypatch):
    """The cross-reference D2(c) requires, exercised with a synthetic row.

    Unreachable from the shipped table -- all nineteen rows are searchable --
    and written anyway, because the first filter-only row (row 96's 44-name
    residue) must land on a refusal that says where the attribute does live,
    not on a KeyError. A synthetic row is the only way to reach it today, and
    a test that cannot reach the branch it names is worse than none.
    """
    from autoposter.collections import filters as module

    row = module.FilterAttribute(
        "aspect", "float", ("movie", "show"), "tier2-deferred", "synthetic",
        search_field=None, show_search_field=None,
        search_kinds=("movie", "show"), filterable=True,
    )
    monkeypatch.setitem(module.BY_NAME, "aspect", row)
    with pytest.raises(ValueError) as error:
        parse_filters({"aspect.gte": 1.78}, searching=True)
    message = str(error.value)
    assert "aspect" in message
    assert "no search field" in message
    assert "filters:" in message


def test_a_filter_refuses_a_search_only_attribute_and_says_where_it_lives():
    with pytest.raises(ValueError) as error:
        parse_filters({"unplayed": True})
    message = str(error.value)
    assert "'unplayed' is a plex_search attribute" in message
    assert "not a client-side filter" in message


def test_a_search_refuses_regex_and_says_why():
    with pytest.raises(ValueError) as error:
        parse_filters({"genre.regex": "^Hor"}, searching=True)
    message = str(error.value)
    assert ".regex" in message
    assert "filters:" in message


def test_a_search_refuses_a_bare_duration_and_names_the_ranges():
    """A CORRECTION to the plan's text, which said a bare ``duration:`` reaches
    Plex unconverted and therefore asks about milliseconds. It does not reach
    Plex at all: ``duration`` is a ``float_attribute`` and takes only the four
    range modifiers (plex.py:549, :600), so a bare ``duration:`` is not in
    ``plex.searches`` and Kometa refuses it outright (builder.py:4194-4195).
    The two blocks agree on the UNIT for the ranges that do exist -- Kometa
    multiplies a search duration by 60000 (builder.py:4234) exactly as the
    client-side view divides by it -- so there is no millisecond trap to warn
    about, and the refusal must not invent one."""
    with pytest.raises(ValueError) as error:
        parse_filters({"duration": 90}, searching=True)
    message = str(error.value)
    assert "filters.duration" in message
    assert "plex_search" in message
    assert "`duration.gt`" in message
    assert "millisecond" not in message


def test_a_filter_refuses_rated_and_points_at_plex_search():
    with pytest.raises(ValueError) as error:
        parse_filters({"critic_rating.rated": True})
    assert "plex_search" in str(error.value)


def test_the_and_suffix_is_refused_in_both_blocks():
    for searching in (True, False):
        with pytest.raises(ValueError) as error:
            parse_filters({"genre.and": ["Horror", "Comedy"]}, searching=searching)
        message = str(error.value)
        assert ".and" in message
        assert "all:" in message


def test_a_boolean_search_takes_a_real_boolean_only():
    predicate = _only(parse_filters({"unplayed": True}, searching=True))
    assert predicate.values == (True,)
    with pytest.raises(ValueError, match="true or false"):
        parse_filters({"unplayed": "yes"}, searching=True)


def test_rated_takes_a_boolean_not_a_number():
    predicate = _only(parse_filters({"critic_rating.rated": False}, searching=True))
    assert predicate.operator == "rated"
    assert predicate.values == (False,)


def test_a_search_list_element_takes_the_written_conjunction_and_is_inline():
    """The nesting divergence, pinned. ``build_filter`` renders each element of
    a list with the WRITTEN key's conjunction (builder.py:4214) and joins them
    with the CONTAINING block's; ``check_filters`` -- the client-side path --
    ANDs each element instead. One grammar, two renderings, one parameter."""
    searched = parse_filters(
        {"any": [{"studio": "A24"}, {"year.gte": 2020}]}, searching=True
    )
    (wrapper,) = searched.children
    assert wrapper.inline is True
    assert wrapper.op == "all"          # the CONTAINING block's op
    assert [child.op for child in wrapper.children] == ["any", "any"]

    filtered = parse_filters({"any": [{"studio": "A24"}, {"year.gte": 2020}]})
    (wrapper,) = filtered.children
    assert wrapper.inline is False
    assert wrapper.op == "any"
    assert [child.op for child in wrapper.children] == ["all", "all"]


def test_a_mapping_shaped_nested_block_is_identical_in_both_modes():
    for searching in (True, False):
        group = parse_filters(
            {"any": {"studio": "A24", "year.gte": 2020}}, searching=searching
        )
        (wrapper,) = group.children
        assert wrapper.op == "any"
        assert wrapper.inline is False
        assert len(wrapper.children) == 2
```

- [ ] **Step 13: Run to see them fail**

```bash
docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_filters.py -k "relative_window or searching or search_refuses or filter_refuses or and_suffix or boolean_search or rated_takes or list_element or mapping_shaped or bare_date"; echo EXIT=$?'
```

Expected: every one of them fails with
`TypeError: parse_filters() got an unexpected keyword argument 'searching'`
or `ImportError: cannot import name 'RelativeWindow'`.

- [ ] **Step 14: Implement the search mode**

Add after `_Today`/`_TODAY` (:645) in `src/autoposter/collections/filters.py`:

```python
@dataclass(frozen=True)
class RelativeWindow:
    """A bare or ``.not`` date in a **search**: "in the last N <unit>".

    The client-side spelling of the same idea is a plain int of days
    (``_as_days``), because ``filters:`` evaluates in python and there is
    nothing to hand a unit to. A search has a server, and Plex takes the unit
    natively -- Kometa sends ``f"{count}{unit}"`` (builder.py:4446-4452) with
    the unit taken from the value's last character. This is the narrowing of
    9a's two ``PLEXAPI_EQUIVALENT`` ``None``s that the roadmap's Notes-for-9b
    item 2 predicted: those two entries mean "plexapi's CLIENT-side table has
    no key", not "Plex cannot do this".

    ``unit`` is a key of ``RELATIVE_UNITS``. Note ``o`` is months and ``m`` is
    minutes; the renderer rewrites ``o`` to ``mon`` on the wire
    (builder.py:4226-4227), which is Plex's spelling and not Kometa's.
    """

    count: int
    unit: str


_WINDOW = re.compile(r"^(\d+)([smhdwoy])$")


def _as_window(value: object, field: str) -> RelativeWindow:
    """A relative window, in Kometa's own spelling.

    A bare int is days, which is both Kometa's default (``search_mod = "d"``,
    builder.py:4447) and the client-side meaning, so the two blocks agree on
    the one spelling an operator is most likely to write. A suffixed string
    names its own unit. Anything else refuses NAMING ALL SEVEN UNITS, because
    ``o`` for months next to ``m`` for minutes is the single least guessable
    thing in this vocabulary and a refusal that does not spell it out sends
    the operator to the source.
    """
    _boolean_is_not_a_value(value, field)
    if isinstance(value, int):
        count, unit = value, "d"
    elif isinstance(value, str):
        text = value.strip().lower()
        match = _WINDOW.match(text)
        if match:
            count, unit = int(match.group(1)), match.group(2)
        elif text.isdigit():
            count, unit = int(text), "d"
        else:
            count = -1
            unit = ""
    else:
        count, unit = -1, ""
    if not unit or count < 0:
        spelled = ", ".join(
            f"{key} = {name.lower()}" for key, name in RELATIVE_UNITS.items()
        )
        raise ValueError(
            f"{field}: {value!r} is not a window -- write a whole number of days "
            f"(30), or a number with a unit ({spelled}). Note that `o` is months "
            "and `m` is minutes"
        )
    return RelativeWindow(count=count, unit=unit)


def _as_bool(value: object, field: str) -> bool:
    """A true/false. The one place in this module where a bool is the value
    rather than the mistake -- see ``_boolean_is_not_a_value``, which every
    other coercion opens with.

    Strings are refused even though Kometa accepts ``t``/``yes``/``n``/``no``
    (util.py:985-995): YAML already turns every spelling an operator would
    naturally write into a real boolean, so accepting the strings would only
    add spellings nobody needs and one more thing for the two systems to
    disagree about.
    """
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field}: {value!r} is not true or false")
```

Replace `_parse_value` (:713-727):

```python
def _parse_value(
    attribute: FilterAttribute,
    operator: str,
    value: object,
    field: str,
    *,
    searching: bool,
) -> object:
    if operator == "regex":
        return _as_regex(value, field)
    if operator == "rated":
        # ``.rated`` is a yes/no question about a FLOAT attribute -- "does this
        # item have a critic rating at all" -- so the value's type has nothing
        # to do with the row's.
        return _as_bool(value, field)
    if attribute.type == "bool":
        return _as_bool(value, field)
    if attribute.type in ("tag", "str"):
        return _as_text(value, field)
    if attribute.type == "int":
        return _as_int(value, field)
    if attribute.type == "float":
        return _as_float(value, field)
    if attribute.type == "duration":
        return _as_minutes(value, field)
    # date
    if operator in ("eq", "not"):
        return _as_window(value, field) if searching else _as_days(value, field)
    return _as_date(value, field)
```

Replace `_split_key` (:730-763):

```python
def _split_key(key: str, field: str, *, searching: bool) -> tuple[FilterAttribute, str]:
    name, _, modifier = key.partition(".")
    attribute = BY_NAME.get(name)
    vocabulary = SEARCHABLE_ATTRIBUTES if searching else FILTERABLE_ATTRIBUTES
    block = "plex_search" if searching else "filters:"
    if attribute is None:
        # The noun keeps 9a's wording for a ``filters:`` block rather than
        # generalising it away, and the block name carries the rest: the two
        # vocabularies are different lists, so offering the whole table here
        # would name attributes the block being parsed cannot take.
        noun = "search" if searching else "filter"
        raise ValueError(
            f"unknown {noun} attribute {name!r} at {field}: the {block} "
            "vocabulary is " + ", ".join(sorted(vocabulary))
        )

    # The cross-reference (D2c). The two vocabularies are DISTINCT and share
    # one table, so an attribute can be legal in one block and refused in the
    # other -- and when it is, the refusal says where it does live rather than
    # reading as a gap. Kometa's own two vocabularies are not nested either:
    # 44 of its filter names have no Plex search field, and 29 of its search
    # names have no filter.
    #
    # The FIRST of the two branches is unreachable with today's nineteen rows:
    # every one of them is searchable, because the fifteen 9a shipped all have
    # Plex search fields. It is written now, and tested with a synthetic row,
    # because the first filter-only attribute (``aspect``, ``height``,
    # ``versions``, ``summary``, ... -- 44 of them, roadmap row 96's residue)
    # will arrive under a table row and must not arrive under a bare KeyError.
    if searching and not attribute.searchable:
        raise ValueError(
            f"{field}: {name!r} is a client-side filter attribute but Plex has "
            "no search field for it, so a plex_search cannot ask for it. Write "
            "it as a `filters:` block on the definition instead -- the search "
            "narrows server-side and the filter refines what comes back"
        )
    if not searching and not attribute.filterable:
        raise ValueError(
            f"{field}: {name!r} is a plex_search attribute (Plex answers it "
            "server-side) and not a client-side filter -- Kometa has no filter "
            "of that name either. Move it into the plex_search builder's "
            "`params`"
        )

    if modifier == "and":
        # Kometa's ``and_searches`` (plex.py:391-407) makes ``genre.and`` mean
        # "every one of these", while a bare ``genre:`` at the top level means
        # "any of these" -- a base conjunction chosen by a suffix on one key.
        # Refused, because the same key spelling would then mean two
        # memberships depending on a suffix three characters long.
        raise ValueError(
            f"{field}: .and is not a modifier here. Kometa uses it to make one "
            f"key ANDed inside an implicit base; write the base out instead -- "
            f"`all:` for every-one-of, `any:` for any-of -- so the config says "
            f"which it is"
        )
    if searching and modifier == "regex":
        raise ValueError(
            f"{field}: .regex is not a plex_search modifier. Kometa's search "
            "regex does not reach Plex at all -- it expands the pattern against "
            "the library's own tag vocabulary first and sends the matching tags "
            "(builder.py:4301-4323) -- so one spelling would mean two "
            "mechanisms. A `filters:` block on the same definition supports "
            ".regex client-side"
        )
    if not searching and modifier in SEARCH_ONLY_OPERATORS:
        # Both halves of the condition are load-bearing, and they are different
        # halves. The TYPE is why the advice can name a numeric comparison at
        # all (``genre.gt: 0`` is meaningless); the OPERATOR is what the rest of
        # the sentence describes -- "has any rating at all", the -1 sentinel.
        # ``SEARCH_ONLY_OPERATORS`` is a one-element tuple today, so testing
        # only the type would put this copy under any second entry that joins
        # it, describing a modifier that is not ``.rated``.
        if attribute.type == "float" and modifier == "rated":
            raise ValueError(
                f"{field}: .{modifier} is a plex_search modifier -- Plex answers "
                f"'has any rating at all' as a server-side comparison against -1 "
                f"and a client-side filter has no equivalent spelling. Write "
                f"`{name}.gt: 0` if that is what you mean"
            )
        raise ValueError(
            f"{field}: .{modifier} is a plex_search modifier -- it has no "
            f"client-side filter equivalent, for {name!r} or any attribute"
        )

    operators = attribute.search_operators if searching else attribute.operators
    default = attribute.default_operator

    if not modifier:
        if default in operators:
            return attribute, default
        # Reached by the four search rows whose search operator set has no
        # blank form: ``duration``, the two ratings and ``plays`` (``plays``'s
        # own TYPE has one, but the row subtracts it -- see the ``int`` note
        # above). The message names the reason rather than the rule, because
        # "not supported" would read as a gap in Plex and it is not one --
        # Kometa refuses the same key, from the same list.
        raise ValueError(
            f"{field}: a bare `{name}:` is not a plex_search. Kometa's search "
            f"vocabulary gives {name!r} its range modifiers and nothing else "
            "(plex.py:594-601), and it checks a written key against exactly "
            "that list (builder.py:4194-4195), so Plex is never asked a plain "
            "equality question about it. Write one of "
            + ", ".join(f"`{name}.{op}`" for op in operators)
        )

    if modifier not in operators or modifier == default:
        writable = [f".{op}" for op in operators if op != default]
        # The bare form's meaning is not literally its "default operator" name
        # for a date -- ``added: 30`` is a window in days, not "added eq 30" --
        # so saying "which means eq" here would teach the wrong thing about
        # what a bare key does.
        bare_meaning = (
            "within-the-last-N-days"
            if attribute.type == "date" and not searching
            else "in-the-last-N"
            if attribute.type == "date"
            else default
        )
        message = (
            f"{field}: .{modifier} does not apply to {name!r}, a "
            f"{attribute.type} attribute in a {block} block "
            "-- it takes " + ", ".join(writable)
        )
        if default in operators:
            message += f" (or no modifier at all, which means {bare_meaning})"
        if attribute.type == "date" and modifier in ("gt", "gte", "lt", "lte"):
            # Kometa accepts all four on a date and rewrites every one of them
            # to the STRICT form (plex.py:2735-2747). Refusing without saying
            # so would look like a gap; the point is that the spelling means
            # something different from what it says, in Kometa as much as here.
            message += (
                f". Kometa accepts .{modifier} on a date but silently rewrites it to "
                ".after/.before, which are strict -- write the strict one you mean, so "
                "the config says what it does"
            )
        if name == "resolution" and modifier == "not":
            message += (
                ". Plex answers no negated resolution filter at all "
                "(Kometa's no_not_mods, plex.py:593)"
            )
        raise ValueError(message)
    return attribute, modifier
```

Thread `searching` through the three parser functions:

```python
def _parse_predicate(key: str, raw: object, field: str, *, searching: bool) -> FilterPredicate:
    attribute, operator = _split_key(key, field, searching=searching)
    written = raw if isinstance(raw, (list, tuple)) else [raw]
    if not written:
        raise ValueError(f"{field}: an empty list matches nothing -- remove the key instead")
    values = tuple(
        _parse_value(attribute, operator, one, field, searching=searching)
        for one in written
    )
    return FilterPredicate(attribute=attribute, operator=operator, values=values, field=field)


def _parse_block(raw: object, op: str, field: str, *, searching: bool) -> FilterGroup:
    """One mapping of ``attribute[.operator]: value`` keys, plus any nested
    ``any:``/``all:`` blocks, combined with ``op``."""
    if not isinstance(raw, Mapping):
        raise ValueError(f"{field}: a filter block is a mapping of attributes, not {raw!r}")
    if not raw:
        raise ValueError(f"{field} is empty -- remove it, or give it an attribute to filter on")
    children: list[FilterGroup | FilterPredicate] = []
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"{field}: {key!r} is not an attribute name")
        if key in ("any", "all"):
            children.append(
                _parse_nested(value, key, f"{field}.{key}", parent_op=op, searching=searching)
            )
        else:
            children.append(
                _parse_predicate(key, value, f"{field}.{key}", searching=searching)
            )
    return FilterGroup(op=op, children=tuple(children), field=field)


def _parse_nested(
    raw: object, op: str, field: str, *, parent_op: str, searching: bool
) -> FilterGroup:
    """An ``any:``/``all:`` value, in either accepted shape.

    A **mapping** makes each of its keys one alternative -- ``any: {studio:
    A24, year.gte: 2020}`` is "from A24 or from this decade". Identical in both
    modes: Kometa's ``util.get_list`` turns a mapping into a one-element list
    and renders it with the written key's conjunction, which is what this is.

    A **list** is where the two blocks diverge, and the divergence is Kometa's,
    not ours:

    - ``filters:`` (Builder.check_filters, builder.py:4674-4754) ANDs each
      element's keys and ORs the elements, so ``any: [{a, b}, {c}]`` is
      ``(a AND b) OR c``. That is 9a's behaviour and its oracle settled it.
    - ``plex_search`` (Builder._filter, builder.py:4207-4217) renders each
      element with the WRITTEN key's conjunction and joins the elements with
      the CONTAINING block's, so the same YAML is ``push(a OR b) <parent> c``.

    One grammar, two renderings, one parameter -- rather than a second parser,
    or a refusal that would cost a Kometa spelling. ``inline`` is how the URL
    builder tells them apart: an inline wrapper's children are spliced into the
    parent's stream instead of getting a ``push``/``pop`` of their own, which
    is what makes the byte-level output match. ``evaluate`` needs no change,
    because an inline wrapper's op equals its parent's and both AND and OR are
    associative.
    """
    if isinstance(raw, Mapping):
        return _parse_block(raw, op, field, searching=searching)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        if not raw:
            raise ValueError(f"{field} is empty -- remove it, or give it a block to filter on")
        element_op = op if searching else "all"
        blocks = tuple(
            _parse_block(one, element_op, f"{field}[{index}]", searching=searching)
            for index, one in enumerate(raw)
        )
        return FilterGroup(
            op=parent_op if searching else op,
            children=blocks,
            field=field,
            inline=searching,
        )
    raise ValueError(f"{field}: expects a mapping of attributes or a list of them, not {raw!r}")


def parse_filters(
    raw: object,
    *,
    field: str = "filters",
    searching: bool = False,
    base: str = "all",
) -> FilterGroup:
    """Parse a ``filters:`` block or a ``plex_search`` block, or refuse naming
    the field that is wrong.

    Every refusal is a ``ValueError`` whose message starts with (or contains)
    the dotted path of the offending key, because the config layer wraps it
    with the definition's title and an operator with twenty definitions needs
    both halves to fix one.

    ``base`` is the top-level conjunction, and it defaults to ``all`` because
    that is what several keys in one ``filters:`` mapping mean in Kometa. A
    ``plex_search`` writes its base out (``all:`` or ``any:``, D1 -- the
    implicit base is refused), and the builder passes the written one here
    rather than nesting the block one level deeper: Kometa's ``base_dict`` IS
    the inner mapping (builder.py:4278-4284), and wrapping it would add a
    ``push``/``pop`` pair Kometa does not emit.

    ``searching`` selects the SEARCH vocabulary and grammar -- see
    ``SEARCH_OPERATORS_BY_TYPE``, ``_as_window`` and ``_parse_nested``. Both are
    keyword-only, and both default to the 9a behaviour its oracle settled.
    """
    if base not in ("any", "all"):
        raise ValueError(f"{field}: {base!r} is not a base -- write `any` or `all`")
    return _parse_block(raw, base, field, searching=searching)
```

Add the test beside the others in `tests/test_collection_filters.py`:

```python
def test_the_base_conjunction_is_the_written_one_and_adds_no_nesting():
    """Kometa's base_dict IS the inner mapping (builder.py:4278-4284), so the
    parsed tree must be one level deep, not two -- an extra level would become
    a push/pop pair the URL builder emits and Kometa does not."""
    group = parse_filters({"studio": "A24", "year.gte": 2020}, base="any", searching=True)
    assert group.op == "any"
    assert len(group.children) == 2
    assert all(isinstance(child, FilterPredicate) for child in group.children)

    with pytest.raises(ValueError, match="is not a base"):
        parse_filters({"studio": "A24"}, base="either")
```

(the import line at the top of the new block becomes
`from autoposter.collections.filters import FilterPredicate, RelativeWindow, parse_filters`)

Add `inline` to `FilterGroup` (:502-508):

```python
@dataclass(frozen=True)
class FilterGroup:
    """``all`` (every child must match) or ``any`` (one child must match).

    ``inline`` is a RENDERING hint and nothing else: it says this group's
    children belong in the parent's stream rather than inside their own
    ``push``/``pop`` pair, which is how a ``plex_search`` list-shaped nesting
    reproduces Kometa byte for byte. It is always False for a ``filters:``
    tree, and ``evaluate`` ignores it -- an inline group's ``op`` equals its
    parent's, and both conjunctions are associative, so the boolean answer is
    the same either way. See ``_parse_nested``.
    """

    op: str
    children: tuple["FilterGroup | FilterPredicate", ...]
    field: str
    inline: bool = False
```

- [ ] **Step 15: Run to green**

```bash
docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_filters.py; echo EXIT=$?'
```

Expected: the whole file green — 9a's existing cases plus the new ones. If any
9a case broke, the `searching=False` default was not preserved; fix that, do
not edit the 9a test.

- [ ] **Step 16: Update the config layer's refusal copy**

In `src/autoposter/config/schema.py`, replace the refusal body inside
`_filters_must_parse_and_be_readable` (:531-543) with:

```python
        for predicate in predicates(parsed):
            row = predicate.attribute
            if row.name in SHIPPED_ATTRIBUTES:
                continue
            if row.source == "tier2-deferred":
                why = (
                    "Phase 9a's probe found the Plex section listing does not carry "
                    "it completely enough to filter on (the row's note in "
                    "collections/filters.py has the numbers), and reading it per "
                    "item would cost one Plex request per item -- so it is filed "
                    "for tier 2 under roadmap row 96 rather than answered wrongly"
                )
            else:
                # ``unprobed``. Deliberately NOT the sentence above: that one
                # cites a probe verdict, and for these rows there is none. 9a
                # probed the seven attributes its own tier named and no others,
                # so claiming a finding here would be the same
                # confident-and-wrong failure the probe exists to prevent.
                why = (
                    "Phase 9a never probed whether the Plex section listing carries "
                    "it, so there is no verdict either way and this service will "
                    "not guess -- it is searchable today through the 'plex_search' "
                    "builder, which asks the server instead"
                )
            raise ValueError(
                f"{self.title!r} cannot filter on {row.name!r} at {predicate.field}: "
                f"that attribute's source tier is {row.source!r}. {why}. "
                "Filterable today: " + ", ".join(SHIPPED_ATTRIBUTES)
            )
```

Note that a `search-only` row never reaches this loop — `parse_filters` refuses
it one layer up, with the message that names `plex_search`. Add the test:

```python
def test_a_definition_filtering_on_plays_says_the_listing_was_never_probed():
    from autoposter.config.schema import CollectionDefinition

    with pytest.raises(ValueError) as error:
        CollectionDefinition(
            title="Rewatched", builder="plex_all", filters={"plays.gt": 3}
        )
    message = str(error.value)
    assert "never probed" in message
    assert "plex_search" in message


def test_a_definition_filtering_on_unplayed_is_refused_by_the_parser():
    from autoposter.config.schema import CollectionDefinition

    with pytest.raises(ValueError) as error:
        CollectionDefinition(
            title="Unseen", builder="plex_all", filters={"unplayed": True}
        )
    assert "not a client-side filter" in str(error.value)
```

(place both in `tests/test_collection_config.py`, beside the existing
`filters:` refusal cases)

- [ ] **Step 17: Full suite, golden, ruff**

```bash
docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9bt1-full test \
    sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9bt1-full
docker logs p9bt1-full | tail -5
docker compose -p p9bt1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9bt1 down
```

Expected: Step 0's baseline count plus the tests this task added, `0 failed`;
`ruff check` prints `All checks passed!`; the golden test is inside the full
run and is green.

- [ ] **Step 18: Commit**

```bash
git add src/autoposter/collections/filters.py src/autoposter/config/schema.py \
        tests/test_collection_filters.py tests/test_collection_config.py
git commit --no-gpg-sign -m "feat(filters): the search grammar -- one parser, two vocabularies

parse_filters(searching=True) selects the search operator sets, the relative-
window date grammar, and Kometa's build_filter nesting rule; every refusal
cross-references the block the attribute does belong to. The .and suffix, the
search .regex and a bare duration: are refused by name.

Transcribed from Kometa v2.4.8 modules/builder.py:4169-4259, :4297-4456 and
modules/plex.py:195, :307, :593, :2735-2751."
```

---

## Task 2: The sort matrices and the limit

**Files:**
- Create: `src/autoposter/collections/search_sorts.py`
- Test: `tests/test_collection_search_sorts.py`

**Interfaces:**
- Consumes: nothing from Task 1 (this module is standalone by design — the
  sorts are a property of the library type, not of the predicate tree).
- Produces:
  - `MOVIE_SORTS: Mapping[str, str]`, `SHOW_SORTS: Mapping[str, str]` — the
    written name → the **pre-encoded** wire value, as a `MappingProxyType`
    (read-only, matching `FILTER_ATTRIBUTES`' frozen idiom one module along).
  - `SortType` — a frozen dataclass with `key: int`, `default_sort: str`,
    `sorts: Mapping[str, str]`.
  - `SORT_TYPES: Mapping[str, SortType]` — keyed on our library type strings
    (`"movie"`, `"show"`, matching `ITEM_KINDS`; a test holds the two key sets
    equal).
  - `KNOWN_SORT_NAMES: frozenset[str]` — the union, for load-time validation.
  - `sort_argument(libtype: str, sort_by: Sequence[str] = ()) -> str`
  - `require_sort_for_libtype(libtype: str, sort_by: Sequence[str]) -> None`
  - `SortNotAvailable(Exception)`

### The naming rule this task exists to hold

**Kometa's key is `sort_by`, never `sort`** (`modules/builder.py:4130`). This
repository already has a `sort` on `CollectionDefinition`
(`src/autoposter/config/schema.py:285`), and it means something else entirely:
the *collection's* Plex display order (`custom`/`release`/`alpha`). The two
coexist and both are honoured (D2d):

| Written | Means | Applied |
| --- | --- | --- |
| `params.sort_by` | the ORDER PLEX RETURNS THE SEARCH IN, which becomes membership order — and, with `params.limit`, decides WHICH items come back | inside the query, this task |
| `params.limit` | how many rows Plex returns | inside the query, this task |
| `sort` (definition) | the collection's display order in Plex | the reconciler, unchanged |
| `limit` (definition) | a cap on resolved members, applied after the client-side filter | `engine.py:476-481`, unchanged |

All four may coexist, and a definition setting `params.limit: 50` and
`limit: 25` gets the 50 Plex ranked highest, then the first 25 of those that
survived resolution and any `filters:` block. The params model's docstring
(Task 4) states that composition; this table is where it is derived.

- [ ] **Step 1: Write the failing test**

Create `tests/test_collection_search_sorts.py`:

```python
"""The sort matrices, checked as a transcription rather than as behaviour.

Nothing here computes anything. The tables are Kometa's, copied PRE-ENCODED --
``%3Adesc`` and ``%2C`` as they stand in ``modules/plex.py`` -- because
re-encoding them here would be a second implementation of a thing that already
has exactly one correct answer, and the ways to get it subtly wrong (encoding
the comma but not the colon, encoding twice) all produce a URL Plex answers
with a plausible, differently-ordered set.
"""
import pytest

from autoposter.collections.search_sorts import (
    KNOWN_SORT_NAMES,
    MOVIE_SORTS,
    SHOW_SORTS,
    SORT_TYPES,
    SortNotAvailable,
    require_sort_for_libtype,
    sort_argument,
)


def test_the_matrices_have_the_recorded_sizes():
    """modules/plex.py:604-636 and :637-667. Fifteen name pairs plus ``random``
    for movies, fourteen plus ``random`` for shows."""
    assert len(MOVIE_SORTS) == 31
    assert len(SHOW_SORTS) == 29


def test_every_name_but_random_is_a_directional_pair():
    for table in (MOVIE_SORTS, SHOW_SORTS):
        directional = [name for name in table if name != "random"]
        assert len(directional) % 2 == 0
        stems = {name.rsplit(".", 1)[0] for name in directional}
        for stem in stems:
            assert f"{stem}.asc" in table
            assert f"{stem}.desc" in table
            # And the two halves are the SAME field. Without this, a transposed
            # or duplicated hand-copy -- ``"year.asc": "year%3Adesc"`` -- passes
            # every other test in this file, which is precisely this table's
            # threat model. Scoped to movie/show on purpose: it does NOT hold
            # for the deferred matrices (plex.py:668-778). Their multi-term
            # values put ``%3Adesc`` on an INNER term and leave the rest of the
            # tie-break chain alone -- ``episode_sorts["show.desc"]`` is
            # ``show.titleSort%3Adesc%2Cseason.index%3AnullsLast%2C...``, six
            # terms, one of which changed. Task 6 must not extend this line.
            assert table[f"{stem}.desc"] == table[f"{stem}.asc"] + "%3Adesc", stem


def test_every_descending_value_carries_the_encoded_colon():
    """``%3Adesc``, not ``:desc``. The watchlist table (plex.py:788-797) uses
    the UNENCODED form for a different endpoint, which is exactly the sort of
    near-miss a hand-retyped table picks up."""
    for table in (MOVIE_SORTS, SHOW_SORTS):
        for name, value in table.items():
            if name.endswith(".desc"):
                assert "%3Adesc" in value, name
                assert ":desc" not in value.replace("%3Adesc", ""), name


def test_the_weird_rows_are_copied_and_not_guessed():
    # resolution sorts by mediaHeight, NOT by videoResolution -- Plex has no
    # sortable resolution field, and the tag values ("4k", "1080", "sd") do not
    # order lexically anyway.
    assert MOVIE_SORTS["resolution.asc"] == "mediaHeight"
    assert MOVIE_SORTS["resolution.desc"] == "mediaHeight%3Adesc"
    # random has no direction at all, in either table.
    assert MOVIE_SORTS["random"] == "random"
    assert SHOW_SORTS["random"] == "random"
    assert "random.asc" not in MOVIE_SORTS
    # release and originally_available are two names for one wire value.
    assert MOVIE_SORTS["release.asc"] == MOVIE_SORTS["originally_available.asc"]
    # the show table's episode_* sorts reach the EPISODE libtype's field.
    assert SHOW_SORTS["episode_added.asc"] == "episode.addedAt"
    # unplayed on a show sorts by the UNVIEWED LEAF COUNT -- how many episodes
    # are unwatched -- which is a number, not the boolean the search attribute
    # of the same name asks about.
    assert SHOW_SORTS["unplayed.asc"] == "unviewedLeafCount"


def test_the_sort_types_carry_kometas_type_numbers():
    """modules/plex.py:779-787. ``type=1``/``type=2`` are what make the query
    return movies/shows rather than the section's default."""
    assert SORT_TYPES["movie"].key == 1
    assert SORT_TYPES["show"].key == 2
    assert SORT_TYPES["movie"].default_sort == "title.asc"
    assert SORT_TYPES["show"].default_sort == "title.asc"


def test_an_absent_sort_by_uses_the_type_default():
    """Every URL Kometa builds carries a sort (builder.py:4140-4141), so ours
    must too -- an omitted sort is not an omitted parameter."""
    assert sort_argument("movie") == "titleSort"
    assert sort_argument("show", ()) == "titleSort"


def test_several_sorts_join_with_an_encoded_comma():
    assert (
        sort_argument("movie", ["critic_rating.desc", "title.asc"])
        == "rating%3Adesc%2CtitleSort"
    )


def test_a_show_only_sort_refuses_on_a_movie_library_naming_both():
    with pytest.raises(SortNotAvailable) as error:
        require_sort_for_libtype("movie", ["episode_added.desc"])
    message = str(error.value)
    assert "episode_added.desc" in message
    assert "movie" in message
    assert "libraries:" in message


def test_a_movie_only_sort_refuses_on_a_show_library():
    with pytest.raises(SortNotAvailable):
        require_sort_for_libtype("show", ["duration.asc"])


def test_a_sort_both_tables_carry_is_accepted_on_both():
    require_sort_for_libtype("movie", ["added.desc"])
    require_sort_for_libtype("show", ["added.desc"])


def test_known_sort_names_is_the_union():
    assert KNOWN_SORT_NAMES == frozenset(MOVIE_SORTS) | frozenset(SHOW_SORTS)
    assert "duration.asc" in KNOWN_SORT_NAMES      # movie only
    assert "episode_added.asc" in KNOWN_SORT_NAMES  # show only
    assert "nonsense.asc" not in KNOWN_SORT_NAMES


def test_the_sort_tables_cover_exactly_the_library_types_the_table_knows():
    """``SORT_TYPES`` restates ``filters.ITEM_KINDS`` and nothing structural
    holds them equal. A third kind joining ``ITEM_KINDS`` would reach
    ``sort_argument`` as a bare ``KeyError`` on the ``SORT_TYPES`` lookup --
    which is the unexplained-failure outcome ``require_sort_for_libtype``'s own
    docstring exists to prevent. The module stays standalone; the contract is
    pinned here."""
    from autoposter.collections.filters import ITEM_KINDS

    assert set(SORT_TYPES) == set(ITEM_KINDS)


def test_asking_a_direction_of_random_is_a_refusal():
    """The module docstring's claim, pinned. ``random`` is the one name with no
    ``.asc``/``.desc`` pair, so ``random.desc`` is not a sort at all -- and the
    refusal must come from the gate rather than from a ``KeyError`` inside
    ``sort_argument``."""
    with pytest.raises(SortNotAvailable) as error:
        require_sort_for_libtype("movie", ["random.desc"])
    assert "random.desc" in str(error.value)

    require_sort_for_libtype("movie", ["random"])


def test_the_deferred_matrices_are_genuinely_absent():
    """The other docstring claim. plex.py:668-778 holds season, episode,
    artist, album and track; v1 searches movie and show, so none of their names
    may have leaked in while the two shipped tables were copied. Every name
    below is real -- it exists in one of those five tables and in neither of
    ours -- so this fails on a leak rather than on a name nobody would type."""
    for name in (
        "season.asc",       # season_sorts, episode_sorts
        "show.asc",         # season_sorts, episode_sorts
        "played.asc",       # artist_sorts, album_sorts, track_sorts
        "album_artist.asc",  # album_sorts, track_sorts
        "popularity.asc",   # track_sorts
    ):
        assert name not in KNOWN_SORT_NAMES
```

- [ ] **Step 2: Run it to see it fail**

```bash
docker compose -p p9bt2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_search_sorts.py; echo EXIT=$?'
```

Expected: collection error —
`ModuleNotFoundError: No module named 'autoposter.collections.search_sorts'`.

- [ ] **Step 3: Write the module**

Create `src/autoposter/collections/search_sorts.py`:

```python
"""``search_sorts``: the sort matrices and the type keys, copied verbatim.

Phase 9b's second layer, and the smallest one. A Plex search URL carries a
``type=`` (which media type the query returns) and a ``sort=`` (which order,
and therefore -- when a ``limit`` is present -- WHICH items). Both are pure
transcription: Plex's sortable field names are underdocumented and several of
them are not the field the sort's NAME suggests, so every table below is
Kometa's own, copied **pre-encoded** from ``modules/plex.py`` at v2.4.8.

**Pre-encoded, never retyped.** The values below contain ``%3Adesc`` exactly
as Kometa stores them, and are joined with ``%2C`` (no value in either table
carries a comma of its own -- only the deferred season/episode/album/track
matrices do). Re-deriving them here -- taking
``rating:desc`` and quoting it at build time -- would be a second
implementation of something with exactly one correct answer, and every way of
getting it subtly wrong produces a URL Plex still answers, with a plausible and
differently-ordered set. Note that ``watchlist_sorts`` (plex.py:788-797) uses
the UNENCODED ``:desc`` for a different endpoint, which is precisely the
near-miss a hand-copy picks up.

**Three rows are worth reading before trusting the rest**, because each is a
name that does not mean what it says:

- ``resolution.asc``/``.desc`` sort by ``mediaHeight``, not by
  ``videoResolution``. Plex has no sortable resolution field, and the tag
  values it does have (``4k``, ``1080``, ``sd``) do not order lexically.
- ``random`` has no direction. It is the one key in either table with no
  ``.asc``/``.desc`` pair, and asking for ``random.desc`` is a refusal.
- ``unplayed`` in the SHOW table is ``unviewedLeafCount`` -- how many episodes
  are unwatched, a number -- while ``unplayed`` as a SEARCH ATTRIBUTE
  (``collections/filters.py``) is a movie-only boolean asking whether the item
  has been played at all. Same word, two meanings, in two tables that sit one
  import apart; they are not related and neither is derived from the other.

The season, episode, artist, album and track matrices (plex.py:668-778) are
deliberately absent: v1 searches movie and show libraries, and a table nothing
can reach is a table nobody checks. They arrive with the libtypes that need
them (the per-family tail, Task 6).

**``sort_by`` is not ``sort``.** Everything in this module serves
``plex_search``'s ``sort_by:`` key -- which order PLEX returns the QUERY in,
and therefore, when a ``limit`` is present, WHICH items the collection gets.
``CollectionDefinition.sort`` (``config/schema.py:289``, default ``custom``)
is a different setting under a confusingly similar name: it is the finished
collection's own display order in Plex, applied with ``collection.sortUpdate``
once the members exist (``collections/lists.py:275``). Kometa keeps them apart
the same way -- ``sort_by`` lives inside the search block (builder.py:4130) --
and the two must never be folded together, because one decides membership and
the other decides presentation.
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

__all__ = [
    "KNOWN_SORT_NAMES",
    "MOVIE_SORTS",
    "SHOW_SORTS",
    "SORT_TYPES",
    "SortNotAvailable",
    "SortType",
    "require_sort_for_libtype",
    "sort_argument",
]

# modules/plex.py:604-636. Fifteen directional pairs plus ``random`` = 31.
#
# Read-only at the type level AND at runtime: ``Mapping`` is what
# ``SortType.sorts`` is annotated as, and ``MappingProxyType`` is what makes
# the annotation true. The repo's neighbouring transcription
# (``filters.FILTER_ATTRIBUTES``) is a tuple of frozen dataclasses for the same
# reason -- a table copied from someone else's source is data, and a caller
# that can edit it can make ours disagree with Kometa at runtime with nothing
# to show for it.
MOVIE_SORTS: Mapping[str, str] = MappingProxyType({
    "title.asc": "titleSort",
    "title.desc": "titleSort%3Adesc",
    "year.asc": "year",
    "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt",
    "originally_available.desc": "originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt",
    "release.desc": "originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating",
    "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating",
    "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",
    "user_rating.desc": "userRating%3Adesc",
    "content_rating.asc": "contentRating",
    "content_rating.desc": "contentRating%3Adesc",
    "duration.asc": "duration",
    "duration.desc": "duration%3Adesc",
    "progress.asc": "viewOffset",
    "progress.desc": "viewOffset%3Adesc",
    "plays.asc": "viewCount",
    "plays.desc": "viewCount%3Adesc",
    "added.asc": "addedAt",
    "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt",
    "viewed.desc": "lastViewedAt%3Adesc",
    # NOT videoResolution -- see the module docstring.
    "resolution.asc": "mediaHeight",
    "resolution.desc": "mediaHeight%3Adesc",
    "bitrate.asc": "mediaBitrate",
    "bitrate.desc": "mediaBitrate%3Adesc",
    # No direction, in either table.
    "random": "random",
})

# modules/plex.py:637-667. Fourteen directional pairs plus ``random`` = 29.
SHOW_SORTS: Mapping[str, str] = MappingProxyType({
    "title.asc": "titleSort",
    "title.desc": "titleSort%3Adesc",
    "year.asc": "year",
    "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt",
    "originally_available.desc": "originallyAvailableAt%3Adesc",
    "episode_originally_available.asc": "episode.originallyAvailableAt",
    "episode_originally_available.desc": "episode.originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt",
    "release.desc": "originallyAvailableAt%3Adesc",
    "episode_release.asc": "episode.originallyAvailableAt",
    "episode_release.desc": "episode.originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating",
    "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating",
    "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",
    "user_rating.desc": "userRating%3Adesc",
    "content_rating.asc": "contentRating",
    "content_rating.desc": "contentRating%3Adesc",
    # A COUNT of unwatched episodes -- not the boolean search attribute of the
    # same name. See the module docstring.
    "unplayed.asc": "unviewedLeafCount",
    "unplayed.desc": "unviewedLeafCount%3Adesc",
    "episode_added.asc": "episode.addedAt",
    "episode_added.desc": "episode.addedAt%3Adesc",
    "added.asc": "addedAt",
    "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt",
    "viewed.desc": "lastViewedAt%3Adesc",
    "random": "random",
})


@dataclass(frozen=True)
class SortType:
    """One library type's search identity.

    ``key`` is the ``type=`` query parameter (modules/plex.py:779-787), which
    is what makes ``/library/sections/N/all`` return movies rather than
    whatever the section's default is. ``default_sort`` is what Kometa applies
    when the config names none (builder.py:4140-4141) -- so an omitted
    ``sort_by`` is not an omitted parameter, it is ``title.asc``.
    """

    key: int
    default_sort: str
    sorts: Mapping[str, str]


SORT_TYPES: Mapping[str, SortType] = MappingProxyType({
    "movie": SortType(key=1, default_sort="title.asc", sorts=MOVIE_SORTS),
    "show": SortType(key=2, default_sort="title.asc", sorts=SHOW_SORTS),
})

# The union, for a LOAD-time check. A definition with no ``libraries:`` key
# runs against every library in the pass, so which table applies is not known
# until build time -- exactly the argument ``require_library_type`` makes
# (builders/base.py:246-269). Load time can still catch a typo that is in
# neither table, which is the common case by a wide margin.
KNOWN_SORT_NAMES: frozenset[str] = frozenset(MOVIE_SORTS) | frozenset(SHOW_SORTS)


class SortNotAvailable(Exception):
    """This sort is real, but not for the library type the pass is running on.

    Its own class rather than a ``ValueError`` so the engine's log line -- which
    carries the exception class name and nothing else -- says what kind of
    failure this was. The same reasoning, and the same shape, as
    ``LibraryTypeMismatch`` (builders/base.py:237-243).
    """


def sort_argument(libtype: str, sort_by: Sequence[str] = ()) -> str:
    """The ``sort=`` value for one query, already encoded.

    Several sorts join with ``%2C`` in the order written, which is Plex's
    tie-break order (builder.py:4289). Duplicates are kept rather than
    deduplicated: Kometa keeps them, and a second mention of the same key is a
    no-op on the server rather than an error, so removing it would be this
    module deciding something Plex already decided.
    """
    table = SORT_TYPES[libtype].sorts
    names = tuple(sort_by) or (SORT_TYPES[libtype].default_sort,)
    return "%2C".join(table[name] for name in names)


def require_sort_for_libtype(libtype: str, sort_by: Sequence[str]) -> None:
    """Refuse a sort this library type has no column for.

    A build-time check, for the reason ``require_library_type`` is one: a
    definition with no ``libraries:`` key applies to every library in the pass,
    so the type is only known here. The refusal matters because the
    alternative is invisible -- a ``KeyError`` deep in ``sort_argument`` would
    reach the engine as a dead source with no explanation, and falling back to
    the default sort would silently build a differently-ordered collection.
    """
    table = SORT_TYPES[libtype].sorts
    for name in sort_by:
        if name in table:
            continue
        others = sorted(
            other for other, spec in SORT_TYPES.items() if name in spec.sorts
        )
        if others:
            kinds = " or ".join(others)
            raise SortNotAvailable(
                f"sort_by {name!r} is a {kinds} sort, but this pass is running "
                f"against a {libtype} library, which has no such column. Narrow "
                f"the definition with `libraries:` so it only targets {kinds} "
                f"libraries, or pick a sort both have"
            )
        raise SortNotAvailable(
            f"sort_by {name!r} is not a sort for a {libtype} library. Options: "
            + ", ".join(sorted(table))
        )
```

- [ ] **Step 4: Run to green**

```bash
docker compose -p p9bt2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_search_sorts.py; echo EXIT=$?'
```

Expected: `11 passed`.

- [ ] **Step 5: Prove the size checksums falsifiable**

```bash
cp src/autoposter/collections/search_sorts.py /tmp/search_sorts.py.bak
```

Delete the two `"bitrate.asc"`/`"bitrate.desc"` lines from `MOVIE_SORTS`.
Rerun Step 4. Expected: `2 failed` —
`test_the_matrices_have_the_recorded_sizes` (`31 != 29`) and
`test_known_sort_names_is_the_union` is unaffected, so expect exactly the size
test plus nothing else; if only one fails that is the correct outcome, record
which. Then:

```bash
cp /tmp/search_sorts.py.bak src/autoposter/collections/search_sorts.py
cmp /tmp/search_sorts.py.bak src/autoposter/collections/search_sorts.py && echo RESTORED
```

Rerun Step 4: `11 passed`. **Paste both real outputs.**

- [ ] **Step 6: Full suite, golden, ruff, teardown**

```bash
docker compose -p p9bt2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9bt2-full test sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9bt2-full
docker logs p9bt2-full | tail -5
docker compose -p p9bt2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9bt2 down
```

Expected: Task 1's count + 11, `0 failed`; `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add src/autoposter/collections/search_sorts.py tests/test_collection_search_sorts.py
git commit --no-gpg-sign -m "feat(search): the movie and show sort matrices, pre-encoded

31 movie sorts and 29 show sorts copied verbatim from Kometa v2.4.8
modules/plex.py:604-667, with the type= keys from :779-787. Kometa's key is
sort_by and not sort -- the definition's existing 'sort' is the collection's
display order and is a different setting.

The three rows whose names mislead (resolution -> mediaHeight, random's
missing direction, show unplayed -> unviewedLeafCount) carry their reason."
```

---

## Task 3: The pure URL builder and THE KOMETA-STRING ORACLE

**Files:**
- Create: `src/autoposter/collections/search_url.py`
- Create: `tests/test_collection_search_url.py`
- Create: `tests/test_collection_search_oracle.py`
- Create: `.superpowers/oracle/9b/kometa_build_filter.py` (not shipped)
- Create: `.superpowers/oracle/9b/ours.py` (not shipped)

**Interfaces:**
- Consumes, from Task 1: `FilterGroup`, `FilterPredicate`, `RelativeWindow`,
  `SEARCH_MODIFIERS`, `RELATIVE_UNITS`, `FilterAttribute.field_for(libtype)`,
  `parse_filters(..., searching=True, base=...)`.
- Consumes, from Task 2: `SORT_TYPES`, `sort_argument`.
- Produces:
  - `TagResolver` — a `Protocol` with
    `def __call__(self, attribute: str, value: str, /) -> tuple[str, ...]: ...`,
    returning the resolved Plex KEYS for one written value (empty = not found;
    several = a language expansion).
  - `build_search_url(group, *, libtype, sort_by=(), limit=None, resolve_tag) -> str`
  - `TagValueNotFound(Exception)`
  - `SearchProducedNothing(Exception)`

### Why the tag resolver is injected

The URL builder is **pure** — this is a hard rule and the whole reason the
oracle can exist. Resolving `genre: Horror` to the key Plex knows it by needs
the library's vocabulary, which is a network call; injecting that as a callable
keeps every path through this module I/O-free, lets the tests and the oracle
pass a dict, and confines the Plex-touching resolver to Task 4's builder where
`require_library_type`-style build-time checks already live.

It returns a **tuple**, not a string, because one written value can legitimately
become several terms: `audio_language: es` expands to every library value whose
base ISO code is `es` (Kometa's `get_language_search_values`,
`plex.py:1321-1344`), and each becomes its own URL term. An ordinary tag
returns a one-element tuple.

### Step 1: Write the failing per-type tests

- [ ] **Step 1**

Create `tests/test_collection_search_url.py`:

```python
"""``search_url`` -- one term per value type, and the assembly around them.

These are unit tests over Kometa's branches, not the oracle. The oracle
(``tests/test_collection_search_oracle.py``) is what proves the whole URL right;
this file is what says WHICH branch broke when it does.
"""
import pytest

from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import (
    SearchProducedNothing,
    TagValueNotFound,
    build_search_url,
)

# The library's tag vocabulary, as a fixture. Real Plex keys are opaque
# integers for most families and the value itself for a few (resolution,
# contentRating, the languages) -- both shapes are represented so a renderer
# that assumed one would fail here.
CHOICES = {
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("label", "Overlay"): ("3",),
    ("collection", "Marvel"): ("77",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
}


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


def url(raw, *, libtype="movie", base="all", **kwargs):
    group = parse_filters(raw, field="params", searching=True, base=base)
    return build_search_url(group, libtype=libtype, resolve_tag=resolve, **kwargs)


def test_a_single_tag_term_carries_the_resolved_key_not_the_written_word():
    """Kometa sends the KEY for a tag in a search (validate_attribute's
    ``title=not plex_search``, builder.py:4412) -- the written word is only
    ever the lookup input."""
    assert url({"genre": "Horror"}) == "?type=1&sort=titleSort&genre=1138"


def test_a_multi_value_tag_joins_with_the_BLOCKS_conjunction():
    """Not with a fixed OR. Under ``all:`` a list is an AND
    (builder.py:4248) -- which is the single most surprising thing in this
    grammar and is Kometa's, not ours."""
    assert url({"content_rating": ["PG-13", "R"]}) == (
        "?type=1&sort=titleSort&contentRating=5&and=1&contentRating=7"
    )
    assert url({"content_rating": ["PG-13", "R"]}, base="any") == (
        "?type=1&sort=titleSort&push=1&contentRating=5&or=1&contentRating=7&pop=1"
    )


def test_a_language_expansion_becomes_one_term_per_variant():
    assert url({"audio_language": "es"}) == (
        "?type=1&sort=titleSort&audioLanguage=es-419&and=1&audioLanguage=es-MX"
        "&and=1&audioLanguage=spa"
    )


def test_an_unresolvable_tag_names_the_value_and_the_attribute():
    with pytest.raises(TagValueNotFound) as error:
        url({"genre": "Horrror"})
    message = str(error.value)
    assert "Horrror" in message
    assert "genre" in message


def test_a_string_value_is_quoted_and_a_tag_value_is_not():
    assert url({"studio.begins": "Warner Bros"}) == (
        "?type=1&sort=titleSort&studio%3C=Warner%20Bros"
    )
    assert url({"studio.not": "Hallmark & Co"}) == (
        "?type=1&sort=titleSort&studio!=Hallmark%20%26%20Co"
    )


def test_a_bare_string_is_a_contains_with_no_modifier_at_all():
    assert url({"studio": "A24"}) == "?type=1&sort=titleSort&studio=A24"


def test_an_int_range_uses_the_encoded_comparison():
    assert url({"year.gte": 2000}) == "?type=1&sort=titleSort&year%3E=2000"
    assert url({"year.lt": 1980}) == "?type=1&sort=titleSort&year%3C%3C=1980"


def test_a_float_renders_as_a_float_including_the_trailing_zero():
    """``util.parse(datatype="float")`` is ``float(str(value))``
    (util.py:861), so Kometa sends ``8.0`` for a written ``8``. Matching that
    exactly is the difference between an identical URL and a similar one."""
    assert url({"critic_rating.gte": 8}) == "?type=1&sort=titleSort&rating%3E=8.0"


def test_a_duration_range_is_minutes_times_sixty_thousand_as_a_float():
    assert url({"duration.gt": 90}) == (
        "?type=1&sort=titleSort&duration%3E%3E=5400000.0"
    )


def test_a_relative_date_window_is_negative_and_carries_its_unit():
    assert url({"added": 30}) == "?type=1&sort=titleSort&addedAt%3E%3E=-30d"
    assert url({"added.not": 30}) == "?type=1&sort=titleSort&addedAt%3C%3C=-30d"


def test_the_months_unit_is_rewritten_to_mon_on_the_wire():
    """Kometa writes ``o`` in the config and ``mon`` on the wire
    (builder.py:4226-4227) -- Plex's spelling, not Kometa's."""
    assert url({"release.not": "6o"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3C%3C=-6mon"
    )
    # every other unit passes through unchanged
    assert url({"release": "2y"}) == (
        "?type=1&sort=titleSort&originallyAvailableAt%3E%3E=-2y"
    )


def test_an_absolute_date_is_iso_whichever_way_it_was_written():
    assert url({"added.before": "12/25/2020"}) == (
        "?type=1&sort=titleSort&addedAt%3C%3C=2020-12-25"
    )


def test_rated_puts_the_negation_on_the_argument_not_the_modifier():
    assert url({"critic_rating.rated": True}) == "?type=1&sort=titleSort&rating!=-1"
    assert url({"critic_rating.rated": False}) == "?type=1&sort=titleSort&rating=-1"


def test_a_boolean_puts_the_negation_on_the_argument_too():
    assert url({"unplayed": True}) == "?type=1&sort=titleSort&unwatched=1"
    assert url({"progress": False}) == "?type=1&sort=titleSort&inProgress!=1"


def test_a_show_library_gets_the_rescoped_fields():
    assert url({"genre": "Drama"}, libtype="show") == (
        "?type=2&sort=titleSort&show.genre=9"
    )
    assert url({"resolution": "1080"}, libtype="show") == (
        "?type=2&sort=titleSort&episode.resolution=1080"
    )
    assert url({"added.after": "2024-01-01"}, libtype="show") == (
        "?type=2&sort=titleSort&show.addedAt%3E%3E=2024-01-01"
    )


def test_a_movie_only_attribute_refuses_on_a_show_library():
    from autoposter.collections.search_url import SearchAttributeNotAvailable

    with pytest.raises(SearchAttributeNotAvailable) as error:
        url({"duration.gt": 90}, libtype="show")
    message = str(error.value)
    assert "duration" in message
    assert "libraries:" in message


def test_a_show_only_attribute_refuses_on_a_movie_library():
    from autoposter.collections.search_url import SearchAttributeNotAvailable

    with pytest.raises(SearchAttributeNotAvailable):
        url({"network": "HBO"}, libtype="movie")


def test_a_mapping_shaped_nesting_gets_one_push_pop_pair():
    assert url({"year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8}}) == (
        "?type=1&sort=titleSort&year%3E=2000&and=1&push=1&studio=A24&and=1"
        "&rating%3E=8.0&pop=1"
    )


def test_a_list_shaped_nesting_gets_one_pair_per_element_joined_by_the_parent():
    """The nesting divergence, at the byte level. Each element carries the
    WRITTEN key's conjunction inside its own push/pop, and the elements are
    joined by the CONTAINING block's -- so an ``any:`` list inside an ``all:``
    base is ANDed at the top and ORed inside. builder.py:4207-4217."""
    assert url({
        "content_rating": "PG-13",
        "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}],
    }) == (
        "?type=1&sort=titleSort&contentRating=5&and=1"
        "&push=1&studio=A24&or=1&year%3E=2020&pop=1"
        "&and=1&push=1&genre=1138&pop=1"
    )


def test_an_any_base_wraps_the_whole_body():
    """builder.py:4288 -- an ``all`` base has its trailing ``&`` stripped, an
    ``any`` base is wrapped in a push/pop instead."""
    assert url({"genre": "Horror", "studio": "A24"}, base="any") == (
        "?type=1&sort=titleSort&push=1&genre=1138&or=1&studio=A24&pop=1"
    )


def test_the_limit_sits_between_the_type_and_the_sort():
    assert url({"year.gte": 2010}, limit=100, sort_by=["critic_rating.desc", "title.asc"]) == (
        "?type=1&limit=100&sort=rating%3Adesc%2CtitleSort&year%3E=2010"
    )


def test_no_built_url_ever_carries_includeCollections():
    """Roadmap Notes-for-9b item 1. The name reads as 'also send each item's
    <Collection> children'; what it actually does is MIX Collection objects
    into the result set, changing what the query returns. 9a's probe hit it
    while trying to rescue the ``collection`` attribute. Nothing in this
    module may reach for it, and this test is the standing guard."""
    built = [
        url({"collection": "Marvel"}),
        url({"collection": "Marvel"}, libtype="show"),
        url({"genre": "Horror", "any": [{"studio": "A24"}, {"label": "Overlay"}]}),
    ]
    for one in built:
        assert "includeCollections" not in one
        assert "include" not in one


def test_a_group_with_no_terms_raises_rather_than_building_an_empty_query():
    """Defensive: the parser refuses an empty block, so this is unreachable
    from a config. Left in because the alternative -- returning
    ``?type=1&sort=titleSort&`` -- is a query that means THE WHOLE LIBRARY,
    which is the single worst thing a broken filter could quietly become."""
    from autoposter.collections.filters import FilterGroup

    empty = FilterGroup(op="all", children=(), field="params")
    with pytest.raises(SearchProducedNothing):
        build_search_url(empty, libtype="movie", resolve_tag=resolve)
```

- [ ] **Step 2: Run to see it fail**

```bash
docker compose -p p9bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_search_url.py; echo EXIT=$?'
```

Expected: collection error —
`ModuleNotFoundError: No module named 'autoposter.collections.search_url'`.

- [ ] **Step 3: Write the module**

Create `src/autoposter/collections/search_url.py`:

```python
"""``search_url``: a parsed predicate tree as a Plex search query string.

Phase 9b's third layer and the one the whole phase's risk sits on. The roadmap
names that risk by name: *silent wrongness -- a mistranslated attribute returns
a plausible-but-wrong item set*. A collection built from a wrong query is not
empty and does not error; it is full, and wrong, and looks exactly like a
correct collection of different titles. So two things are true of this module
and both are load-bearing:

**It is pure.** No plexapi, no HTTP, no clock, no config. Its inputs are a
``FilterGroup`` (Task 1's parser), a library type, a sort list, a limit, and a
``resolve_tag`` CALLABLE. That purity is what lets
``tests/test_collection_search_oracle.py`` compare its output, byte for byte,
against strings produced by Kometa's own ``build_filter`` running standalone --
which is the only test that can catch a translation this module and its unit
tests would agree on and Plex would not.

**Every branch is a transcription with a line reference.** The assembly is
``modules/builder.py:4169-4295`` at Kometa v2.4.8; the per-type special cases
are :4222-4250; the final wrap is :4287-4289. Where this module differs from
Kometa it is because 9a or 9b refused something (``.regex``, ``validate:``,
the implicit base, a bare ``duration:``), never because a branch was
simplified.

**What Kometa does here and this module deliberately does not:**

- ``validate: false`` (builder.py:4160-4167) downgrades every per-attribute
  error to a log line and CONTINUES, so a typo silently narrows nothing. This
  module has no such switch -- a value that does not resolve raises.
- Kometa skips a term whose validation came back empty (``continue``,
  :4220-4221), which is how a ``validate: false`` run ends up with a query
  missing a clause it was asked for. Nothing here can produce an empty term.
- ``includeCollections`` appears nowhere in Kometa's search path either, and
  ``test_no_built_url_ever_carries_includeCollections`` keeps it that way here:
  it does not enrich a listing, it MIXES Collection objects into the result set
  (roadmap Notes-for-9b item 1).
"""
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import quote

from autoposter.collections.filters import (
    RELATIVE_UNITS,
    SEARCH_MODIFIERS,
    FilterGroup,
    FilterPredicate,
    RelativeWindow,
)
from autoposter.collections.search_sorts import SORT_TYPES, sort_argument

__all__ = [
    "SearchAttributeNotAvailable",
    "SearchProducedNothing",
    "TagResolver",
    "TagValueNotFound",
    "build_search_url",
]


class TagResolver(Protocol):
    """The library's own tag vocabulary, as a callable.

    Returns the Plex KEYS one written value resolves to -- empty when the
    library has no such value, and SEVERAL when the value expands (a language
    code covering every locale variant the library carries). Injected rather
    than imported so this module stays pure; Task 4's builder passes the
    Plex-backed, run-cached one and the tests pass a dict.
    """

    def __call__(self, attribute: str, value: str, /) -> tuple[str, ...]: ...


class TagValueNotFound(Exception):
    """A written tag value is not in the library's vocabulary.

    Kometa raises here too (``Plex Error: {attribute}: {value} not found``,
    builder.py:4433-4437) and this service did not, before 9b: it compared
    case-insensitively at evaluation time instead, so a typo produced an empty
    collection rather than a refusal (roadmap row 158). A search has to resolve
    the value -- Plex is sent a key, not a word -- so the refusal comes for
    free on this path and stays filed on the other.
    """


class SearchAttributeNotAvailable(Exception):
    """This attribute is real, but Plex will not answer it for this library
    type. Its own class so the engine's class-name-only log line says so; the
    same shape as ``LibraryTypeMismatch`` (builders/base.py:237-243)."""


class SearchProducedNothing(Exception):
    """The tree rendered to no terms at all.

    Unreachable from a validated config (the parser refuses an empty block),
    and raised rather than returned because the alternative -- a query with a
    ``type`` and a ``sort`` and no predicate -- means THE WHOLE LIBRARY.
    Kometa's equivalent is ``FilterFailed`` (builder.py:4291).
    """


def build_search_url(
    group: FilterGroup,
    *,
    libtype: str,
    sort_by: Sequence[str] = (),
    limit: int | None = None,
    resolve_tag: TagResolver,
) -> str:
    """The query string for one search, from ``?`` onward.

    Appended by the caller to ``/library/sections/{key}/all``, which is what
    Kometa does with it (plex.py:958-959).

    Assembly order is Kometa's and is not arbitrary: ``type`` first, then
    ``limit`` if there is one, then ``sort`` (always -- an omitted ``sort_by``
    means the type's default, never no sort), then the body
    (builder.py:4287-4289). An ``all`` base has its trailing ``&`` stripped; an
    ``any`` base is wrapped in ``push=1&...pop=1`` instead, because a top-level
    OR needs a scope and the query string has no other way to give it one.
    """
    body = _render_group(group, libtype=libtype, resolve_tag=resolve_tag)
    if not body:
        raise SearchProducedNothing(
            "this search built no query terms at all, which Plex would answer "
            "with the entire library"
        )
    tail = body[:-1] if group.op == "all" else f"push=1&{body}pop=1"
    head = f"?type={SORT_TYPES[libtype].key}&"
    if limit is not None:
        head += f"limit={limit}&"
    head += f"sort={sort_argument(libtype, sort_by)}&"
    return head + tail


def _render_group(group: FilterGroup, *, libtype: str, resolve_tag: TagResolver) -> str:
    """One block, as terms joined by its own conjunction. Always ends in ``&``.

    ``conjunction`` goes BEFORE each term after the first, which is Kometa's
    shape (builder.py:4248, :4252) and not the more obvious "join with a
    separator" -- the difference shows up in the nested case, where the
    conjunction that joins two ``push``/``pop`` pairs belongs to the block
    containing them.
    """
    conjunction = "and=1&" if group.op == "all" else "or=1&"
    out = ""
    for child in group.children:
        if isinstance(child, FilterGroup):
            inner = _render_group(child, libtype=libtype, resolve_tag=resolve_tag)
            if not inner:
                continue
            # An INLINE wrapper is a list-shaped ``any:``/``all:`` in a
            # plex_search: its children each got their own push/pop already,
            # and Kometa joins them with the containing block's conjunction
            # rather than scoping them together (builder.py:4207-4217). Giving
            # it a pair of its own would add a nesting level Kometa never
            # emits. See ``filters._parse_nested``.
            piece = inner if child.inline else f"push=1&{inner}pop=1&"
        else:
            piece = _render_predicate(child, group.op, libtype=libtype, resolve_tag=resolve_tag)
        if not piece:
            continue
        out += (conjunction if out else "") + piece
    return out


def _render_predicate(
    predicate: FilterPredicate,
    block_op: str,
    *,
    libtype: str,
    resolve_tag: TagResolver,
) -> str:
    """One ``attribute[.operator]: value`` line, as one or more terms.

    More than one when the value is a list, or when a tag value expands -- and
    they are joined by the BLOCK's conjunction, not by a fixed OR
    (builder.py:4245-4248). Under an ``all:`` block that makes
    ``content_rating: [PG-13, R]`` an AND, which is the most surprising thing
    in this grammar and is Kometa's.
    """
    row = predicate.attribute
    if libtype not in row.search_kinds:
        kinds = " or ".join(sorted(row.search_kinds))
        raise SearchAttributeNotAvailable(
            f"{predicate.field}: Plex answers {row.name!r} only for {kinds} "
            f"libraries, and this pass is running against a {libtype} library, "
            f"where the query would match nothing at all. Narrow the definition "
            f"with `libraries:` so it only targets {kinds} libraries"
        )
    field = row.field_for(libtype)
    conjunction = "and=1&" if block_op == "all" else "or=1&"
    args = _arguments(predicate, resolve_tag=resolve_tag)
    return "".join(
        (conjunction if index else "") + f"{field}{modifier}={value}&"
        for index, (modifier, value) in enumerate(args)
    )


def _arguments(
    predicate: FilterPredicate, *, resolve_tag: TagResolver
) -> list[tuple[str, str]]:
    """``(modifier, value)`` pairs for one predicate, in Kometa's branch order.

    The order matters: ``.rated`` is tested before the boolean branch because a
    ``.rated`` predicate's VALUE is a boolean and would otherwise take it, and
    the date branch is tested before everything because a relative window is a
    date value with a modifier the table's own lookup would get wrong.
    """
    row = predicate.attribute
    operator = predicate.operator
    modifier = SEARCH_MODIFIERS[(row.type, operator)]

    # A relative window: negative, unit-suffixed, and ``o`` becomes ``mon``.
    # builder.py:4222-4233.
    if row.type == "date" and operator in ("eq", "not"):
        out = []
        for value in predicate.values:
            assert isinstance(value, RelativeWindow)  # the parser guarantees it
            unit = "mon" if value.unit == "o" else value.unit
            assert value.unit in RELATIVE_UNITS
            out.append((modifier, f"-{value.count}{unit}"))
        return out

    # "Has any rating at all", as a comparison against Plex's sentinel.
    # builder.py:4236-4237. The negation is on the ARGUMENT's modifier slot,
    # which is why SEARCH_MODIFIERS holds "" for this pair.
    if operator == "rated":
        return [("!" if value else "", "-1") for value in predicate.values]

    # builder.py:4238-4241, and the same argument-side negation.
    if row.type == "bool":
        return [("" if value else "!", "1") for value in predicate.values]

    # MINUTES to MILLISECONDS. ``predicate.values`` are floats (9a's
    # ``_as_minutes``), and Kometa's are too (``float(str(value))``,
    # util.py:861), so the product renders with a trailing ``.0`` -- which is
    # what Kometa sends. builder.py:4234-4235.
    if row.type == "duration":
        return [(modifier, str(value * 60000)) for value in predicate.values]

    # An absolute date, ISO, whichever spelling it was written in.
    # builder.py:4441-4445.
    if row.type == "date":
        return [(modifier, value.isoformat()) for value in predicate.values]

    # A tag: the library's KEY, never the written word, and possibly several
    # per value. builder.py:4400-4440, :4245-4248.
    if row.type == "tag":
        out = []
        for value in predicate.values:
            keys = resolve_tag(row.name, value)
            if not keys:
                raise TagValueNotFound(
                    f"{predicate.field}: {value!r} is not one of the "
                    f"{row.name} values this library uses, so Plex has no key "
                    "to search for. Check the spelling against the library's "
                    "own list"
                )
            out.extend((modifier, key) for key in keys)
        return out

    # A string: quoted, unresolved. builder.py:4246.
    if row.type == "str":
        return [(modifier, quote(str(value))) for value in predicate.values]

    # int and float, as they stand -- a float keeps its ``.0``.
    return [(modifier, str(value)) for value in predicate.values]
```

- [ ] **Step 4: Run to green**

```bash
docker compose -p p9bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_search_url.py; echo EXIT=$?'
```

Expected: `23 passed`. If a per-type test disagrees with the code, **the test's
expected string was hand-derived from Kometa's source and the code is what
moves** — unless the oracle (below) says otherwise, in which case the oracle
wins and both change.

- [ ] **Step 5: Commit the builder**

```bash
git add src/autoposter/collections/search_url.py tests/test_collection_search_url.py
git commit --no-gpg-sign -m "feat(search): the pure URL builder

A parsed predicate tree to a Plex query string, transcribed branch by branch
from Kometa v2.4.8 modules/builder.py:4169-4295. No plexapi, no HTTP, no clock
-- the tag vocabulary arrives as an injected callable, which is what makes the
Kometa-string oracle possible at all.

A standing test asserts no built query carries includeCollections."
```

### THE KOMETA-STRING ORACLE

This is the acceptance gate for the whole phase. Everything above asserts the
transcription against itself; this asserts it against Kometa.

**The thirteen configs.** Each is written twice — once in this service's
spelling, once in Kometa's — because the two grammars differ in exactly the
places 9a and 9b refused something, and an oracle driven by a config Kometa
cannot parse proves nothing. Between them they cover: both base conjunctions,
both nesting shapes, both library types, every value type's special case, sorts
(single and multiple) and a limit.

| # | Exercises | Ours | Kometa's |
| --- | --- | --- | --- |
| 1 | multi-value tag under `all` (the AND surprise), key resolution | `{"all": {"content_rating": ["PG-13", "R"]}}` | identical |
| 2 | `any` base, string default-contains, int `.gte`, `sort_by`, `limit` | `{"any": {"studio": "A24", "year.gte": 2020}, "sort_by": "critic_rating.desc", "limit": 25}` | identical |
| 3 | list-shaped `any:` inside an `all` base — the nesting divergence | `{"all": {"content_rating": "PG-13", "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}]}}` | identical |
| 4 | mapping-shaped nested `all:` — one push/pop | `{"all": {"year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8}}}` | identical |
| 5 | relative windows, all three units in play, the `o`→`mon` rewrite | `{"all": {"added": 30, "release.not": "6o", "last_played.not": "2y"}}` | identical |
| 6 | absolute dates, both written spellings | `{"all": {"release.after": "2000-01-01", "added.before": "12/25/2020"}}` | identical |
| 7 | duration ×60000, and the float rendering | `{"all": {"duration.gt": 90, "duration.lte": "2:30"}}` | `{"all": {"duration.gt": 90, "duration.lte": 150}}` — `2:30` is 9a's own written form (`_as_minutes`), which Kometa has no spelling for; the MINUTE VALUE is identical, which is the point |
| 8 | `.rated`, both polarities | `{"all": {"critic_rating.rated": True, "audience_rating.rated": False}}` | identical |
| 9 | booleans, both polarities | `{"all": {"unplayed": True, "progress": False}}` | identical |
| 10 | string quoting, three string modifiers | `{"all": {"studio.begins": "Warner Bros", "studio.not": "Hallmark & Co", "studio.is": "A24"}}` | identical |
| 11 | several sorts, joined `%2C`, with a limit | `{"all": {"year.gte": 2010}, "sort_by": ["critic_rating.desc", "title.asc"], "limit": 100}` | identical |
| 12 | SHOW libtype: five re-scoped fields, a show-only attribute, a show sort | `{"all": {"genre": "Drama", "resolution": "1080", "audio_language": "en", "network": "HBO", "added.after": "2024-01-01"}, "sort_by": "episode_added.desc", "limit": 10}` | identical |
| 13 | a language expansion — several terms from one written value, ANDed | `{"all": {"audio_language": "es"}}` | identical |

**The vocabulary fixture.** Both sides resolve tags through the same fixed
mapping, written once and shared by value rather than by import (the oracle
imports nothing from this repo, so it carries its own copy and a test asserts
the two copies are equal):

```python
CHOICES = {
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
}
```

- [ ] **Step 6: Write the oracle driver**

Create `.superpowers/oracle/9b/kometa_build_filter.py`. It is Kometa's
`build_filter` and the branches of `validate_attribute` the thirteen configs
reach, transcribed standalone with the Kometa infrastructure removed (logging,
the display strings, the music/season/episode libtypes, the TMDb/actor-id
lookups, `validate=False`). Header, verbatim:

```python
"""THE ORACLE -- Kometa's own build_filter, transcribed standalone.

Provenance: every table and every branch below is copied from Kometa v2.4.8
(https://raw.githubusercontent.com/Kometa-Team/Kometa/v2.4.8), fetched
2026-08-26. The line references are that tag's:

  modules/plex.py:60-138      search_translation
  modules/plex.py:168-193     show_translation
  modules/plex.py:195         modifier_translation
  modules/plex.py:307         date_sub_mods
  modules/plex.py:430-445     movie_only_searches
  modules/plex.py:446-506     show_only_searches
  modules/plex.py:507-601     the category lists and the ``searches`` set
  modules/plex.py:604-667     movie_sorts, show_sorts
  modules/plex.py:779-787     sort_types
  modules/plex.py:2735-2751   Plex.split
  modules/builder.py:4092-4295  Builder.build_filter
  modules/builder.py:4297-4456  Builder.validate_attribute (the reached branches)
  modules/util.py:256-287     get_list
  modules/util.py:299-307     validate_date
  modules/util.py:859-875     check_int
  modules/util.py:911-1034    parse (the int/float/bool branches)
  modules/request.py:37-38    quote

NOTHING from the autoposter repository is imported. Unlike 9a's oracle this one
does not import plexapi either: ``build_filter`` reads a config dict and a tag
vocabulary and writes a string -- there is no Plex object anywhere in it.

The library's tag vocabulary is supplied by ``CHOICES`` below rather than by
``get_search_choices``, because that function's only job is to turn a written
word into the key Plex knows it by, and pinning the mapping is what makes the
comparison about TRANSLATION rather than about a server's contents.

Run:  python kometa_build_filter.py
      -> prints one line per config: ``N <url>``
"""
```

The driver's structure, in full:

```python
import os
from datetime import datetime
from urllib.parse import quote

# --- the vocabulary fixture, shared BY VALUE with tests/test_collection_search_oracle.py
CHOICES = {
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
}
```

then, copied verbatim from the fetched files and NOT retyped: the whole of
`search_translation`, `show_translation`, `modifier_translation`,
`date_sub_mods`, `movie_only_searches`, `show_only_searches`,
`string_attributes`, `string_modifiers`, `boolean_attributes`,
`date_attributes`, `date_modifiers`, `year_attributes`, `number_attributes`,
`number_modifiers`, `float_attributes`, `float_modifiers`, `tag_attributes`,
`tag_modifiers`, `no_not_mods`, the `searches` comprehension, `movie_sorts`,
`show_sorts` and `sort_types`;

then `split`, `validate_attribute` and `build_filter` with these and only these
removals, each marked with a `# REMOVED:` comment saying what and why:

- every `logger.*` call and the whole `display`/`filter_details`/`display_out`
  half (they build a human-readable summary, not the URL);
- the music libtypes and `track_only_searches` (v1 searches movies and shows);
- the `folder_location` branch of `get_search_key` (it calls `listFilters`;
  `folder_location` is deferred by name and no config reaches it);
- `get_actor_id`/`tmdb_attributes` (no config reaches them);
- `validate=False` handling (this service refuses the switch, so the oracle
  always raises);
- `get_search_choices` and `get_language_search_values`, replaced by a lookup
  into `CHOICES` that returns `[(written, key), ...]` in the same shape
  `validate_attribute` produces (`plex_search=True` → pairs, key second).

Finally:

```python
CONFIGS = [
    # ... the thirteen from the table above, in Kometa's spelling, each as
    # (libtype, plex_filter)
]

def main():
    for index, (libtype, plex_filter) in enumerate(CONFIGS, start=1):
        _, _, url = build_filter("plex_search", plex_filter, libtype)
        print(f"{index} {url}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run the oracle and capture its output**

```bash
docker compose -p p9bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test python .superpowers/oracle/9b/kometa_build_filter.py
```

Expected: thirteen lines, each `N ?type=...`. Paste the **raw output verbatim**
into the task report — it is the derivation, and a reviewer must be able to see
it without rerunning anything.

The plan's own hand-derivation of six of the thirteen, from Kometa's source,
is below. **If the oracle disagrees with any of these, the ORACLE wins**, the
divergence is written onto the code it corrects with a `SETTLED-BY-ORACLE`
marker (9a's convention, `src/autoposter/collections/filters.py:36-48`), and
this plan document is edited in the same commit:

```
1  ?type=1&sort=titleSort&contentRating=5&and=1&contentRating=7
2  ?type=1&limit=25&sort=rating%3Adesc&push=1&studio=A24&or=1&year%3E=2020&pop=1
3  ?type=1&sort=titleSort&contentRating=5&and=1&push=1&studio=A24&or=1&year%3E=2020&pop=1&and=1&push=1&genre=1138&pop=1
5  ?type=1&sort=titleSort&addedAt%3E%3E=-30d&and=1&originallyAvailableAt%3C%3C=-6mon&and=1&lastViewedAt%3C%3C=-2y
7  ?type=1&sort=titleSort&duration%3E%3E=5400000.0&and=1&duration%3C=9000000.0
12 ?type=2&limit=10&sort=episode.addedAt%3Adesc&show.genre=9&and=1&episode.resolution=1080&and=1&episode.audioLanguage=en&and=1&show.network=42&and=1&show.addedAt%3E%3E=2024-01-01
```

- [ ] **Step 8: Write the oracle test with the captured strings pinned as data**

Create `tests/test_collection_search_oracle.py`. Docstring first, following
`tests/test_collection_filter_oracle.py`'s shape (the provenance table, what
was found on the first run, what the fixture models rather than copies), then:

```python
import pytest

from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import build_search_url

# The library's tag vocabulary. A COPY of the oracle driver's ``CHOICES``,
# shared by value and not by import, because the driver imports nothing from
# this repository and must not import anything into it either. The next test
# holds the two copies equal.
CHOICES = {
    ("content_rating", "PG-13"): ("5",),
    ("content_rating", "R"): ("7",),
    ("genre", "Horror"): ("1138",),
    ("genre", "Drama"): ("9",),
    ("resolution", "1080"): ("1080",),
    ("network", "HBO"): ("42",),
    ("audio_language", "en"): ("en",),
    ("audio_language", "es"): ("es-419", "es-MX", "spa"),
}


def resolve(attribute, value, /):
    return CHOICES.get((attribute, value), ())


# (id, libtype, params) -- OUR spelling. Config 7 is the one place the two
# spellings differ: ``2:30`` is 9a's written duration form and Kometa has no
# equivalent, so the oracle is driven with the same value written ``150``.
CONFIGS = [
    ("1-multi-value-tag", "movie", {"all": {"content_rating": ["PG-13", "R"]}}),
    ("2-any-base", "movie", {
        "any": {"studio": "A24", "year.gte": 2020},
        "sort_by": "critic_rating.desc", "limit": 25,
    }),
    ("3-list-nesting", "movie", {"all": {
        "content_rating": "PG-13",
        "any": [{"studio": "A24", "year.gte": 2020}, {"genre": "Horror"}],
    }}),
    ("4-mapping-nesting", "movie", {"all": {
        "year.gte": 2000, "all": {"studio": "A24", "critic_rating.gte": 8},
    }}),
    ("5-relative-dates", "movie", {"all": {
        "added": 30, "release.not": "6o", "last_played.not": "2y",
    }}),
    ("6-absolute-dates", "movie", {"all": {
        "release.after": "2000-01-01", "added.before": "12/25/2020",
    }}),
    ("7-duration", "movie", {"all": {"duration.gt": 90, "duration.lte": "2:30"}}),
    ("8-rated", "movie", {"all": {
        "critic_rating.rated": True, "audience_rating.rated": False,
    }}),
    ("9-booleans", "movie", {"all": {"unplayed": True, "progress": False}}),
    ("10-string-quoting", "movie", {"all": {
        "studio.begins": "Warner Bros",
        "studio.not": "Hallmark & Co",
        "studio.is": "A24",
    }}),
    ("11-several-sorts", "movie", {
        "all": {"year.gte": 2010},
        "sort_by": ["critic_rating.desc", "title.asc"], "limit": 100,
    }),
    ("12-show-rescoping", "show", {
        "all": {
            "genre": "Drama", "resolution": "1080", "audio_language": "en",
            "network": "HBO", "added.after": "2024-01-01",
        },
        "sort_by": "episode_added.desc", "limit": 10,
    }),
    ("13-language-expansion", "movie", {"all": {"audio_language": "es"}}),
]

# KOMETA'S OWN ANSWERS, pinned as data. Produced by
# ``.superpowers/oracle/9b/kometa_build_filter.py`` -- Kometa v2.4.8's
# ``build_filter``, transcribed standalone, importing nothing from this
# repository. The raw run is in the Task 3 report. Do not edit a string here to
# make a test pass: if ours differs, ours is wrong.
KOMETA = {
    # <paste the thirteen lines from Step 7, as "id": "url",>
}


@pytest.mark.parametrize(("name", "libtype", "params"), CONFIGS, ids=[c[0] for c in CONFIGS])
def test_our_url_is_byte_identical_to_kometas(name, libtype, params):
    base = "all" if "all" in params else "any"
    group = parse_filters(params[base], field="params", searching=True, base=base)
    sort_by = params.get("sort_by") or ()
    if isinstance(sort_by, str):
        sort_by = [sort_by]
    ours = build_search_url(
        group,
        libtype=libtype,
        sort_by=sort_by,
        limit=params.get("limit"),
        resolve_tag=resolve,
    )
    assert ours == KOMETA[name]


def test_the_oracles_vocabulary_fixture_matches_this_files_copy():
    """The driver imports nothing from here and this file imports nothing from
    there, so the shared fixture is shared by VALUE. This reads the driver as
    text and compares the literal, which is the only coupling that does not
    break the isolation."""
    import ast
    from pathlib import Path

    source = Path(".superpowers/oracle/9b/kometa_build_filter.py").read_text()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "CHOICES":
            assert ast.literal_eval(node.value) == CHOICES
            return
    pytest.fail("the oracle driver has no CHOICES literal")


def test_every_oracle_url_is_free_of_includeCollections():
    for url in KOMETA.values():
        assert "includeCollections" not in url


def test_the_thirteen_configs_cover_every_shipped_value_type():
    """Coverage, asserted rather than claimed. If a later task adds a value
    type to the table, this fails until a config exercises it through the
    oracle."""
    from autoposter.collections.filters import FILTER_ATTRIBUTES

    exercised = set()
    for _, _, params in CONFIGS:
        base = "all" if "all" in params else "any"
        stack = [params[base]]
        while stack:
            block = stack.pop()
            for key, value in block.items():
                if key in ("any", "all"):
                    stack.extend(value if isinstance(value, list) else [value])
                    continue
                name = key.split(".")[0]
                exercised.add(next(r.type for r in FILTER_ATTRIBUTES if r.name == name))
    assert exercised == {"tag", "str", "int", "float", "date", "duration", "bool"}
```

- [ ] **Step 9: Run the oracle test**

```bash
docker compose -p p9bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_collection_search_oracle.py; echo EXIT=$?'
```

Expected: `16 passed` (thirteen parametrized cases + three structural).

**If it is red — and expect it to be, at least once.** 9a's oracle was red in
four places on its first run, every one a transcription judgement made without
the source in front of it, and every one adjudicated in Kometa's favour. Follow
the same procedure: for each divergence, read the Kometa line that produces it,
change OUR code, and write the adjudication onto the code it fixed with a
`SETTLED-BY-ORACLE` marker naming the line. Never edit `KOMETA` to match.
Record every one in the task report with both strings.

- [ ] **Step 10: Prove the oracle falsifiable**

```bash
cp src/autoposter/collections/search_url.py /tmp/search_url.py.bak
```

In `_arguments`, change the duration branch from `value * 60000` to
`int(value * 60000)`. Rerun Step 9. Expected: `1 failed` —
`test_our_url_is_byte_identical_to_kometas[7-duration]`, with
`duration%3E%3E=5400000` against `duration%3E%3E=5400000.0`. This is the exact
class of near-miss the oracle exists for: the query still works, still returns
films, and returns a set nobody would notice was different. Then:

```bash
cp /tmp/search_url.py.bak src/autoposter/collections/search_url.py
cmp /tmp/search_url.py.bak src/autoposter/collections/search_url.py && echo RESTORED
```

Rerun Step 9: `16 passed`. **Paste both real outputs.**

- [ ] **Step 11: Full suite, golden, ruff, teardown**

```bash
docker compose -p p9bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9bt3-full test sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9bt3-full
docker logs p9bt3-full | tail -5
docker compose -p p9bt3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9bt3 down
```

Expected: Task 2's count + 39 (23 unit + 16 oracle), `0 failed`;
`All checks passed!`.

- [ ] **Step 12: Commit**

```bash
git add tests/test_collection_search_oracle.py .superpowers/oracle/9b/
git commit --no-gpg-sign -m "test(search): the Kometa-string oracle

Thirteen configs, thirteen golden URI strings produced by Kometa v2.4.8's own
build_filter transcribed standalone and pinned as data. Covers both bases, both
nesting shapes, both library types, every value type's special case, sorts and
limits.

This converts the roadmap's named 9b risk -- silent wrongness, a mistranslated
attribute returning a plausible-but-wrong set -- into a gate."
```

---

## Task 4: The `plex_search` builder, its params, and the config surface

**Files:**
- Create: `src/autoposter/collections/builders/plex_search.py`
- Modify: `src/autoposter/collections/builders/__init__.py` (one import, one
  `register(...)`, three `__all__` entries)
- Modify: `pyproject.toml` — only if Step 0 chooses option A
- Create: `tests/test_builder_plex_search.py`
- Modify: `tests/test_collection_config.py` (extend)

**Interfaces:**
- Consumes: Task 1's `parse_filters(..., searching=True, base=...)`,
  `BY_NAME`, `SEARCHABLE_ATTRIBUTES`; Task 2's `KNOWN_SORT_NAMES`,
  `require_sort_for_libtype`, `SortNotAvailable`; Task 3's
  `build_search_url`, `TagValueNotFound`, `SearchAttributeNotAvailable`.
- Produces:
  - `PlexSearchParams(BaseModel)` — `all`, `any`, `sort_by`, `limit`;
    `extra="forbid"`.
  - `PlexSearchBuilder` with `type_name = "plex_search"` and
    `params_model = PlexSearchParams`.
  - `PlexSearchUnavailable(Exception)`, `PlexSearchRefused(Exception)`.

### Step 0: THE `langcodes` DECISION — resolve before writing any code

`audio_language`/`subtitle_language` are in D3's v1 scope, and Kometa's search
for them does **not** send the written code: it expands it against the
library's own values through `get_language_search_values`
(`modules/plex.py:1321-1344`), whose base-code reduction is
`langcodes.Language.get(value).language` (`plex.py:141-151`). `langcodes` is
**not** a dependency of this repository.

Three options. **Default: A.** A reviewer who prefers B or C says so before
this task starts, because it is a table cell and a dependency, not an
implementation detail.

- **A — add `langcodes` to `pyproject.toml` dependencies.** Fidelity is exact
  and free; the cost is one new runtime dependency (pure-python, no C
  extension, MIT/Apache) and a container rebuild. Chosen by default because
  hand-transcribing the ~184-row ISO 639-2→639-1 map is precisely the
  fallible-transcription failure mode this phase exists to prevent, and a map
  that is 95% right produces a language collection that is full and wrong.
- **B — ship the locale half only.** `es-419`/`es_MX` reduce by splitting on
  `-`/`_`, which needs no table; three-letter codes (`spa`, `ger`, `deu`) do
  not reduce and fall through to Kometa's own unparseable fallback (the value
  unchanged). Costs: a library whose Plex values are three-letter codes gets no
  expansion, silently. Only defensible if Task 5's probe #2 shows this library
  reports locale tags exclusively — which is why B is a *deferral to after
  Task 5*, not a v1 answer.
- **C — defer both language attributes to the per-family tail.** They keep
  `searchable=True` in the table but the builder refuses them by name until
  Task 5's probe answers. Costs the two catalog rows this phase was supposed to
  unblock.

If A: add `"langcodes>=3.4",` to `dependencies` in `pyproject.toml` (keeping
the list's existing alphabetical order and comment style), then rebuild:

```bash
docker compose -p p9bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml build test
```

Record the choice and the reasoning in the task report. Every code block below
is written for **A**; under B or C, delete `_base_language_code`'s `langcodes`
import and replace its body (B) or delete `_language_keys` and add the refusal
(C), and say so in the module docstring.

### Step 1: Write the failing tests for the params model

- [ ] **Step 1**

Create `tests/test_builder_plex_search.py`:

```python
"""``plex_search`` -- the builder, its params, and its refusals.

The URL itself is proven elsewhere (``tests/test_collection_search_oracle.py``,
byte-identical against Kometa). What is proven here is the surface an operator
touches: which spellings load, which refuse and what they say, what the builder
asks Plex, and how many times it asks.
"""
import pytest
from pydantic import ValidationError

from autoposter.collections.builders.plex_search import (
    PlexSearchParams,
    PlexSearchBuilder,
)


def test_a_base_is_required_and_named():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"genre": "Horror"})
    message = str(error.value)
    assert "any:" in message and "all:" in message


def test_two_bases_are_refused():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate(
            {"all": {"genre": "Horror"}, "any": {"studio": "A24"}}
        )
    assert "one base" in str(error.value)


def test_the_implicit_base_is_refused_with_kometas_rule_spelled_out():
    """D1. Kometa lets you omit the base and then splits the keys itself: a
    bare ``genre:`` becomes an OR block and ``genre.and:`` an AND one
    (builder.py:4261-4276), so the same key means two memberships depending on
    a three-character suffix. Refused, and the message says what to write."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"genre": "Horror", "studio": "A24"})
    message = str(error.value)
    assert "Kometa" in message
    assert "all:" in message


def test_validate_false_is_refused_by_name():
    """D1. Kometa's ``validate: false`` (builder.py:4160-4167) downgrades every
    per-attribute error to a log line and builds the query WITHOUT the clause
    it could not resolve -- a narrower collection than the config asks for,
    with no failure anywhere."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "validate": False})
    message = str(error.value)
    assert "validate" in message
    assert "silently" in message


def test_the_type_key_is_refused_by_name():
    """Kometa's ``type:`` (builder.py:4109-4121) selects the season/episode/
    album/track libtype. v1 searches movies and shows, and accepting the key
    while ignoring it would be a setting that reads as applied and is not."""
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "type": "episode"})
    assert "type" in str(error.value)


def test_an_unknown_params_key_is_refused():
    with pytest.raises(ValidationError):
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "sort": "title.asc"})


def test_the_key_is_sort_by_and_not_sort():
    """The naming warning, held. ``sort`` on the DEFINITION is the collection's
    Plex display order; ``sort_by`` in PARAMS is the query's order, which
    decides membership when a limit is present. Writing ``sort`` here is a
    typo with a plausible-looking effect, so it refuses."""
    params = PlexSearchParams.model_validate(
        {"all": {"genre": "Horror"}, "sort_by": "title.asc"}
    )
    assert params.sort_by == ["title.asc"]


def test_a_scalar_sort_by_becomes_a_one_element_list():
    params = PlexSearchParams.model_validate(
        {"all": {"genre": "Horror"}, "sort_by": "added.desc"}
    )
    assert params.sort_by == ["added.desc"]


def test_an_unknown_sort_name_refuses_at_load():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate(
            {"all": {"genre": "Horror"}, "sort_by": "titel.asc"}
        )
    assert "titel.asc" in str(error.value)


def test_a_sort_only_one_libtype_has_still_loads():
    """It cannot refuse here: a definition with no ``libraries:`` key runs
    against every library in the pass, so which table applies is only known at
    build time. The build-time refusal is tested below."""
    PlexSearchParams.model_validate(
        {"all": {"genre": "Horror"}, "sort_by": "episode_added.desc"}
    )


def test_a_limit_of_zero_refuses():
    with pytest.raises(ValidationError):
        PlexSearchParams.model_validate({"all": {"genre": "Horror"}, "limit": 0})


def test_a_bad_attribute_inside_the_block_refuses_at_load_naming_the_key():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"aspect": "1.78"}})
    message = str(error.value)
    assert "aspect" in message
    assert "params.all" in message
    assert "plex_search" in message


def test_a_bad_modifier_inside_the_block_refuses_at_load():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"genre.begins": "Hor"}})
    assert "genre" in str(error.value)


def test_a_search_regex_refuses_at_load_pointing_at_filters():
    with pytest.raises(ValidationError) as error:
        PlexSearchParams.model_validate({"all": {"studio.regex": "pictures$"}})
    assert "filters:" in str(error.value)
```

- [ ] **Step 2: Run to see it fail**

```bash
docker compose -p p9bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_builder_plex_search.py; echo EXIT=$?'
```

Expected: collection error — `ModuleNotFoundError: No module named
'autoposter.collections.builders.plex_search'`.

### Step 3: Write the builder module

- [ ] **Step 3**

Create `src/autoposter/collections/builders/plex_search.py`:

```python
"""``plex_search``: a query language against the library, answered by Plex.

The one builder whose membership the SERVER decides. Every other builder in
this package fetches a list from somewhere and hands the engine ids to resolve;
this one asks the library a question and hands back the rating keys of whatever
answered -- ``("plex", ratingKey)`` ids, which the engine resolves through its
own owned index exactly like any other builder's, so the engine keeps one shape
and this builder gets the ownership, dry-run and reconcile behaviour for free.

**The server narrows, the client refines.** A ``filters:`` block on a
``plex_search`` definition stays CLIENT-side and runs after resolution, exactly
as it does on any other definition (``engine.py:457-474``). The two are not
folded into one query, deliberately: query-folding is an optimisation with a
very large correctness surface -- the two vocabularies are not the same set,
the two evaluate their dates in different clocks, and a fold that got either
wrong would produce a full, plausible, wrong collection. So a definition may
carry both, and each does its own job.

**The two vocabularies share one table and are not the same set.** Which
attributes a search may name is ``FILTER_ATTRIBUTES``'s ``searchable`` column
and which a ``filters:`` block may name is its ``filterable`` column
(``collections/filters.py``). Kometa's own two vocabularies are not nested
either -- 44 of its filter names have no Plex search field and 29 of its search
names have no filter -- so every refusal on either side cross-references the
other rather than reading as a gap.

**Dates are answered in the PLEX SERVER's clock, not the runner's** (roadmap
row 154, and D6: document the divergence, do not reconcile it). ``added.after:
2026-06-01`` in a ``plex_search`` is decided by the server; the same line in a
``filters:`` block is decided by the process running this service, and 9a
established that the value it compares is already in the runner's local clock
because plexapi converts Plex's epoch with a bare ``datetime.fromtimestamp``.
The two can disagree about an item near a boundary. The relative-window forms
(``added: 30``, ``last_played.not: 6o``) are day-granular or coarser and are
therefore insensitive to the offset, which is why the params docstring
recommends them.

**Tag values are validated against the library's own vocabulary at BUILD time**
(roadmap row 158). A search sends Plex a KEY, never a written word, so the
lookup is not an extra check bolted on -- it is how the query gets built at all.
One ``listFilterChoices`` per (library, field, libtype) per pass, memoised in
``ctx.run_cache``, failures included. Load time cannot do it: the library is
not known until the pass, which is the same reason ``require_library_type``
is a build-time check (``builders/base.py:246-269``).
"""
import logging
from typing import Any

import langcodes
from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoposter.collections.builders.base import BuilderContext, BuilderResult
from autoposter.collections.filters import BY_NAME, parse_filters
from autoposter.collections.search_sorts import KNOWN_SORT_NAMES, require_sort_for_libtype
from autoposter.collections.search_url import build_search_url

logger = logging.getLogger(__name__)

__all__ = [
    "PlexSearchBuilder",
    "PlexSearchParams",
    "PlexSearchRefused",
    "PlexSearchUnavailable",
]

# The keys Kometa accepts and this builder refuses, each with the reason. Held
# as data so the refusals cannot drift apart in wording, and so adding one is
# a row rather than a branch.
_REFUSED_KEYS: dict[str, str] = {
    "validate": (
        "`validate: false` tells Kometa to log a per-attribute error and carry on "
        "(modules/builder.py:4160-4167), which builds the query WITHOUT the clause "
        "it could not resolve -- a narrower collection than the config asks for, "
        "with nothing failing anywhere. This service refuses silently-wrong "
        "membership: fix the value, or remove the clause"
    ),
    "type": (
        "`type:` selects the season, episode, album or track libtype "
        "(modules/builder.py:4109-4121). This builder searches movie and show "
        "libraries; accepting the key and ignoring it would be a setting that "
        "reads as applied and is not"
    ),
}


class PlexSearchUnavailable(Exception):
    """The context carries no library accessor, or Plex would not answer.

    Its own class so the engine's log line -- which carries the exception class
    name and nothing else -- says which client was missing. Never carries a
    Plex exception's message: those can contain a tokenised URL.
    """


class PlexSearchRefused(Exception):
    """The query cannot be built for the library this pass is running on."""


class PlexSearchParams(BaseModel):
    """``plex_search``'s params: the base, the query, the order and the cap.

    ``all:`` or ``any:`` -- exactly one, written out. Kometa also accepts the
    base being OMITTED and then chooses one per key (a bare ``genre:`` becomes
    an OR block, ``genre.and:`` an AND one, modules/builder.py:4261-4276);
    refused here, because the same key would mean two different memberships
    depending on a three-character suffix.

    ``sort_by`` -- **not** ``sort``. Kometa's key is ``sort_by``
    (modules/builder.py:4130) and this definition already has a ``sort``, which
    is the *collection's* Plex display order and a completely different
    setting. All four narrowing knobs may coexist, and they compose in this
    order:

    1. ``params.sort_by`` decides the order Plex returns the search in;
    2. ``params.limit`` caps what Plex returns, so with a ``sort_by`` it
       decides WHICH items come back -- "the 50 highest-rated" is these two
       together and nothing else can express it;
    3. the definition's ``filters:`` block refines what came back, client-side;
    4. the definition's ``limit`` caps the members that survived, and the
       definition's ``sort`` sets how Plex displays them.

    So ``params.limit: 50`` with ``limit: 25`` means "ask Plex for its top 50,
    keep the first 25 of those this library still owns and the filter kept".

    **Dates:** prefer the relative windows (``added: 30``,
    ``last_played.not: 6o``) over the absolute ``.before``/``.after`` forms.
    A server-side date predicate is evaluated in the PLEX SERVER's clock and a
    ``filters:`` one in the runner's (roadmap row 154); a window measured in
    days or longer is insensitive to that offset and a same-day boundary is
    not. ``o`` is months and ``m`` is minutes -- the units are Kometa's
    (modules/plex.py:307).

    ``extra="forbid"`` so ``sort:`` for ``sort_by:`` is an error rather than a
    silently-ignored key -- the same failure ``PlexIdParams`` exists to catch
    (builders/base.py:299-312), one level down.
    """

    model_config = ConfigDict(extra="forbid")

    all: dict | None = None
    any: dict | None = None
    sort_by: list[str] | None = None
    limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _the_base_and_the_refused_keys(cls, data: Any) -> Any:
        """Everything that has to beat ``extra="forbid"``.

        A ``mode="before"`` validator, and that placement is the whole point:
        with no base written, EVERY key in the mapping is an extra field, so
        pydantic's own answer would be "Extra inputs are not permitted:
        genre, studio" -- which is true and tells an operator nothing about
        what a plex_search actually wants. The three keys Kometa does have get
        a reason here for the same reason.
        """
        if not isinstance(data, dict):
            return data
        lowered = {str(key).lower(): key for key in data}
        for refused, why in _REFUSED_KEYS.items():
            if refused in lowered:
                raise ValueError(f"{lowered[refused]!r} is not accepted here. {why}")
        if "sort" in lowered and "sort_by" not in lowered:
            raise ValueError(
                "the search's order is `sort_by`, not `sort` -- `sort` on the "
                "definition itself is the collection's display order in Plex, "
                "which is a different setting and is still available"
            )
        bases = [name for name in ("all", "any") if name in lowered]
        if len(bases) == 2:
            raise ValueError(
                "a plex_search has one base: write `all:` (every clause must "
                "match) or `any:` (one must), not both. Kometa refuses this too "
                "(modules/builder.py:4106-4107)"
            )
        if not bases:
            raise ValueError(
                "a plex_search needs a base. Write the clauses under `all:` if "
                "every one must match, or under `any:` if one is enough. Kometa "
                "lets you omit it and then picks per key -- a bare `genre:` "
                "becomes an OR and `genre.and:` an AND "
                "(modules/builder.py:4261-4276) -- which makes one spelling mean "
                "two memberships, so it is not accepted here"
            )
        if isinstance(data.get("sort_by"), str):
            data = {**data, "sort_by": [data["sort_by"]]}
        return data

    @model_validator(mode="after")
    def _the_block_must_parse_as_a_search(self) -> "PlexSearchParams":
        """The whole vocabulary check, at LOAD.

        Unknown attribute, a modifier the type does not take in a search, an
        unparseable value, a `.regex`, a `.and`, a bare `duration:` -- every one
        of them refuses here, naming the key, hours before the pass. What
        CANNOT be checked here is anything that needs the library: which
        libtype, and whether a tag value exists. Those are build-time, and the
        module docstring says why.
        """
        base = "all" if self.all is not None else "any"
        block = self.all if self.all is not None else self.any
        parse_filters(block, field=f"params.{base}", searching=True, base=base)
        return self

    @model_validator(mode="after")
    def _sort_names_must_exist_somewhere(self) -> "PlexSearchParams":
        for name in self.sort_by or []:
            if name not in KNOWN_SORT_NAMES:
                raise ValueError(
                    f"sort_by {name!r} is not a Plex sort. Options: "
                    + ", ".join(sorted(KNOWN_SORT_NAMES))
                )
        return self

    @property
    def base(self) -> str:
        return "all" if self.all is not None else "any"

    @property
    def block(self) -> dict:
        return self.all if self.all is not None else self.any


def _base_language_code(value: str) -> str:
    """A language value in any common form, reduced to its base ISO 639-1 code.

    Transcribed from Kometa's ``base_language_code`` (modules/plex.py:141-151),
    including its fallback: a value that cannot be parsed comes back unchanged,
    so an unrecognised code targets itself rather than nothing. ``langcodes`` is
    the same library Kometa uses -- see the Task 4 Step 0 decision record for
    why it was added rather than transcribed.
    """
    if not value:
        return value
    try:
        return langcodes.Language.get(str(value)).language or value
    except ValueError:
        return value


class PlexSearchBuilder:
    """The library, asked a question."""

    type_name = "plex_search"
    params_model = PlexSearchParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = PlexSearchParams.model_validate(ctx.config)
        libtype = ctx.library_type.lower()
        if libtype not in ("movie", "show"):
            raise PlexSearchRefused(
                f"the 'plex_search' builder searches movie and show libraries, "
                f"but this pass is running against a {ctx.library_type} library. "
                "Narrow the definition with `libraries:`"
            )
        access = ctx.sources.plex
        if access is None:
            raise PlexSearchUnavailable(
                "the 'plex_search' builder reads the library it is running "
                "against, and this context carries no library accessor"
            )
        section = access.section()

        require_sort_for_libtype(libtype, params.sort_by or [])
        group = parse_filters(
            params.block, field=f"params.{params.base}", searching=True, base=params.base
        )
        url = build_search_url(
            group,
            libtype=libtype,
            sort_by=params.sort_by or (),
            limit=params.limit,
            resolve_tag=_Resolver(ctx, section, libtype),
        )
        logger.debug("plex_search: %s", url)
        try:
            items = section.fetchItems(
                f"/library/sections/{section.key}/all{url}"
            )
        except Exception as error:  # noqa: BLE001 -- class name only, never the message
            raise PlexSearchUnavailable(
                "Plex would not answer this search: "
                f"{type(error).__name__}"
            ) from None
        ids = [("plex", str(item.ratingKey)) for item in items]
        logger.debug("plex_search: %d item(s)", len(ids))
        return BuilderResult(ids=ids)


class _Resolver:
    """The library's tag vocabulary, cached per pass.

    One ``listFilterChoices`` per (library, libtype-scope, field) per pass, and
    the FAILURE is memoised too -- ``BuilderContext.run_cache``'s own docstring
    requires that, because a dead source re-fetched once per collection is the
    defect the cache exists to prevent.

    The lookup is keyed on **four** spellings per choice -- ``title``, ``key``,
    and both lowercased -- which is Kometa's (``get_search_choices``,
    modules/plex.py:1308-1315), and is where the case-insensitivity an operator
    sees actually lives. Later choices overwrite earlier ones on a collision,
    also Kometa's (plain assignment, not ``setdefault``).
    """

    def __init__(self, ctx: BuilderContext, section, libtype: str) -> None:
        self._ctx = ctx
        self._section = section
        self._libtype = libtype

    def __call__(self, attribute: str, value: str, /) -> tuple[str, ...]:
        row = BY_NAME[attribute]
        field = row.field_for(self._libtype)
        scope, _, name = field.rpartition(".")
        scope = scope or self._libtype
        if attribute in ("audio_language", "subtitle_language"):
            return self._language_keys(attribute, scope, name, value)
        choices = self._choices(attribute, scope, name)
        for spelling in (str(value), str(value).lower()):
            if spelling in choices:
                return (choices[spelling],)
        return ()

    def _cache_key(self, scope: str, name: str) -> str:
        return f"plex_search:choices:{self._ctx.library}:{scope}:{name}"

    def _raw_choices(self, attribute: str, scope: str, name: str):
        key = self._cache_key(scope, name)
        cached = self._ctx.run_cache.get(key, _MISSING)
        if cached is not _MISSING:
            if isinstance(cached, Exception):
                raise cached
            return cached
        try:
            found = list(self._section.listFilterChoices(field=name, libtype=scope))
        except Exception as error:  # noqa: BLE001 -- class name only
            failure = PlexSearchUnavailable(
                f"Plex has no {attribute!r} filter for this library "
                f"({type(error).__name__}), so its values cannot be resolved"
            )
            self._ctx.run_cache[key] = failure
            raise failure from None
        self._ctx.run_cache[key] = found
        return found

    def _choices(self, attribute: str, scope: str, name: str) -> dict[str, str]:
        table: dict[str, str] = {}
        for choice in self._raw_choices(attribute, scope, name):
            for spelling in (
                str(choice.title), str(choice.key),
                str(choice.title).lower(), str(choice.key).lower(),
            ):
                table[spelling] = str(choice.key)
        return table

    def _language_keys(
        self, attribute: str, scope: str, name: str, value: str
    ) -> tuple[str, ...]:
        """Kometa's ``get_language_search_values`` (modules/plex.py:1321-1344).

        An EXACT value Plex reports (``es-419``, ``spa``) targets only itself;
        anything else expands to every library value that reduces to the same
        base code. Each becomes its own URL term -- and under an ``all:`` block
        those terms are ANDed, which is Kometa's behaviour and is Task 5's
        probe #2: an item is unlikely to carry three Spanish variants at once.
        """
        exact: dict[str, str] = {}
        by_base: dict[str, list[str]] = {}
        for choice in self._raw_choices(attribute, scope, name):
            key = str(choice.key)
            exact[key.lower()] = key
            by_base.setdefault(_base_language_code(key.lower()), []).append(key)
        code = str(value).lower()
        if code != _base_language_code(code) and code in exact:
            return (exact[code],)
        return tuple(by_base.get(code, ()))


_MISSING = object()
```

- [ ] **Step 4: Register it**

In `src/autoposter/collections/builders/__init__.py`, add the import in
alphabetical position (after `plex_id`'s block, before `plex_watchlist`):

```python
from autoposter.collections.builders.plex_search import PlexSearchBuilder
```

and the registration beside the other Plex builders (after
`register(PlexAllBuilder())`):

```python
# The one builder whose membership Plex decides. Registered here like every
# other -- it produces ids, not actions, so it is a plain Builder and not the
# SmartBuilder escape hatch, which is 9c's question and not this one's.
register(PlexSearchBuilder())
```

- [ ] **Step 5: Run the params tests to green**

```bash
docker compose -p p9bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_builder_plex_search.py; echo EXIT=$?'
```

Expected: `14 passed`.

### Step 6: The build-time tests

- [ ] **Step 6: Write them**

Append to `tests/test_builder_plex_search.py`:

```python
from autoposter.collections.builders.base import BuilderContext, SourceClients
from autoposter.collections.builders.sources_bundle import PlexSectionAccess

# No ``@pytest.mark.asyncio`` anywhere below: this suite runs pytest-asyncio in
# ``asyncio_mode = "auto"`` (pyproject.toml:51), so an ``async def test_`` is
# collected as one already and the marker would be noise.


class FakeChoice:
    def __init__(self, title, key):
        self.title = title
        self.key = key


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeSection:
    """Counts what it was asked, so 'one lookup per pass' is a measurement."""

    key = 1

    def __init__(self, choices=None, items=None, raise_on=()):
        self._choices = choices or {}
        self._items = items or [FakeItem(11), FakeItem(12)]
        self._raise_on = set(raise_on)
        self.filter_calls = []
        self.fetch_calls = []

    def listFilterChoices(self, field, libtype=None):
        self.filter_calls.append((field, libtype))
        if field in self._raise_on:
            raise LookupError("no such filter field")
        return self._choices.get((field, libtype), [])

    def fetchItems(self, key):
        self.fetch_calls.append(key)
        return self._items


def context(section, *, library_type="Movie", config=None, run_cache=None):
    return BuilderContext(
        library="Movies",
        library_type=library_type,
        config=config or {},
        run_cache=run_cache if run_cache is not None else {},
        sources=SourceClients(plex=PlexSectionAccess(section, lambda: {})),
    )


GENRES = [FakeChoice("Horror", "1138"), FakeChoice("Drama", "9")]


async def test_the_builder_sends_the_query_to_the_sections_all_endpoint():
    section = FakeSection(choices={("genre", "movie"): GENRES})
    ctx = context(section, config={"all": {"genre": "Horror"}})
    result = await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=1&sort=titleSort&genre=1138"
    ]
    assert result.ids == [("plex", "11"), ("plex", "12")]


async def test_a_tag_value_is_looked_up_once_per_pass_and_cached():
    section = FakeSection(choices={("genre", "movie"): GENRES})
    run_cache = {}
    for _ in range(3):
        ctx = context(
            section,
            config={"all": {"genre": ["Horror", "Drama"]}},
            run_cache=run_cache,
        )
        await PlexSearchBuilder().build(ctx)
    assert section.filter_calls == [("genre", "movie")]


async def test_a_failed_lookup_is_cached_too_so_a_dead_field_is_asked_once():
    """``BuilderContext.run_cache``'s own docstring requires this: a builder
    that memoises must memoise the failure, or a dead source is re-fetched once
    per collection."""
    section = FakeSection(raise_on={"genre"})
    run_cache = {}
    for _ in range(3):
        ctx = context(section, config={"all": {"genre": "Horror"}}, run_cache=run_cache)
        with pytest.raises(Exception):
            await PlexSearchBuilder().build(ctx)
    assert section.filter_calls == [("genre", "movie")]


async def test_a_misspelled_tag_value_refuses_at_build_naming_value_and_attribute():
    """Roadmap row 158, answered on this path. Kometa raises
    ``Plex Error: genre: Horrror not found`` (builder.py:4433); before 9b this
    service compared case-insensitively at evaluation time instead, so a typo
    built an empty collection rather than failing."""
    section = FakeSection(choices={("genre", "movie"): GENRES})
    ctx = context(section, config={"all": {"genre": "Horrror"}})
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "Horrror" in message
    assert "genre" in message


async def test_the_lookup_matches_kometas_four_spellings():
    section = FakeSection(choices={("genre", "movie"): GENRES})
    for written in ("Horror", "horror", "1138"):
        ctx = context(section, config={"all": {"genre": written}})
        await PlexSearchBuilder().build(ctx)
    assert all("genre=1138" in call for call in section.fetch_calls)


async def test_a_show_library_asks_the_rescoped_field_at_the_rescoped_libtype():
    section = FakeSection(choices={("resolution", "episode"): [FakeChoice("1080", "1080")]})
    ctx = context(section, library_type="Show", config={"all": {"resolution": "1080"}})
    await PlexSearchBuilder().build(ctx)
    assert section.filter_calls == [("resolution", "episode")]
    assert section.fetch_calls == [
        "/library/sections/1/all?type=2&sort=titleSort&episode.resolution=1080"
    ]


async def test_a_show_only_attribute_refuses_at_build_on_a_movie_library():
    section = FakeSection()
    ctx = context(section, config={"all": {"network": "HBO"}})
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    assert "libraries:" in str(error.value)


async def test_a_show_only_sort_refuses_at_build_on_a_movie_library():
    section = FakeSection(choices={("genre", "movie"): GENRES})
    ctx = context(
        section,
        config={"all": {"genre": "Horror"}, "sort_by": "episode_added.desc"},
    )
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    assert "episode_added.desc" in str(error.value)


async def test_a_music_library_refuses_by_name():
    section = FakeSection()
    ctx = context(section, library_type="Artist", config={"all": {"genre": "Horror"}})
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    assert "movie and show" in str(error.value)


async def test_a_context_with_no_library_accessor_says_so():
    ctx = BuilderContext(
        library="Movies", library_type="Movie", config={"all": {"genre": "Horror"}}
    )
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    assert "no library accessor" in str(error.value)


async def test_a_plex_failure_is_reported_by_class_name_and_nothing_else():
    """Secrets hygiene: a plexapi exception's message can carry a tokenised
    URL, so the refusal carries the class name and no other part of it."""
    class Boom(Exception):
        def __str__(self):
            return "http://plex.example:32400/library?X-Plex-Token=SECRET"

    section = FakeSection(choices={("genre", "movie"): GENRES})

    def explode(key):
        raise Boom()

    section.fetchItems = explode
    ctx = context(section, config={"all": {"genre": "Horror"}})
    with pytest.raises(Exception) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "Boom" in message
    assert "SECRET" not in message
    assert "X-Plex-Token" not in message


async def test_a_language_code_expands_to_every_variant_the_library_carries():
    section = FakeSection(choices={("audioLanguage", "movie"): [
        FakeChoice("Spanish", "es-419"),
        FakeChoice("Spanish (Mexico)", "es-MX"),
        FakeChoice("Spanish", "spa"),
        FakeChoice("English", "en"),
    ]})
    ctx = context(section, config={"all": {"audio_language": "es"}})
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=1&sort=titleSort"
        "&audioLanguage=es-419&and=1&audioLanguage=es-MX&and=1&audioLanguage=spa"
    ]


async def test_an_exact_language_value_targets_only_itself():
    section = FakeSection(choices={("audioLanguage", "movie"): [
        FakeChoice("Spanish", "es-419"),
        FakeChoice("Spanish (Mexico)", "es-MX"),
    ]})
    ctx = context(section, config={"all": {"audio_language": "es-419"}})
    await PlexSearchBuilder().build(ctx)
    assert section.fetch_calls == [
        "/library/sections/1/all?type=1&sort=titleSort&audioLanguage=es-419"
    ]
```

- [ ] **Step 7: Run and iterate to green**

```bash
docker compose -p p9bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_plex_search.py; echo EXIT=$?'
```

Expected: `27 passed`.

### Step 8: The config surface

- [ ] **Step 8: Write the config tests**

Append to `tests/test_collection_config.py`:

```python
def test_a_plex_search_definition_validates_its_params_at_load():
    from autoposter.config.schema import CollectionDefinition

    definition = CollectionDefinition(
        title="Recent Horror",
        builder="plex_search",
        params={"all": {"genre": "Horror", "added": 90}, "sort_by": "added.desc"},
    )
    assert definition.builder == "plex_search"


def test_a_plex_search_definition_with_a_bad_key_names_the_title_and_the_key():
    from autoposter.config.schema import CollectionDefinition

    with pytest.raises(ValueError) as error:
        CollectionDefinition(
            title="Recent Horror",
            builder="plex_search",
            params={"all": {"genre": "Horror"}, "sort": "added.desc"},
        )
    message = str(error.value)
    assert "Recent Horror" in message
    assert "sort_by" in message


def test_a_plex_search_definition_may_also_carry_a_client_side_filters_block():
    """D2(b): the server narrows and the client refines. Both are honoured and
    neither is folded into the other."""
    from autoposter.config.schema import CollectionDefinition

    definition = CollectionDefinition(
        title="Recent Horror, well rated",
        builder="plex_search",
        params={"all": {"genre": "Horror"}},
        filters={"audience_rating.gte": 7},
    )
    assert definition.filters == {"audience_rating.gte": 7}


def test_plex_search_is_not_a_smart_builder_so_the_membership_knobs_apply():
    """A smart definition refuses limit/sync_mode/item_label/filters
    (schema.py:416-459) because Plex owns its membership. plex_search resolves
    real members through the engine, so all four mean what they always did."""
    from autoposter.config.schema import CollectionDefinition

    definition = CollectionDefinition(
        title="Top 25 Horror",
        builder="plex_search",
        params={"all": {"genre": "Horror"}, "sort_by": "critic_rating.desc", "limit": 50},
        limit=25,
        sync_mode="append",
        item_label=["Horror night"],
    )
    assert definition.limit == 25
    assert definition.params["limit"] == 50
```

- [ ] **Step 9: Run the config tests**

```bash
docker compose -p p9bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 600 pytest -q tests/test_collection_config.py; echo EXIT=$?'
```

Expected: the whole file green, including the four new cases. No change to
`schema.py` should be needed — the params-model hook (`:362-414`) already picks
up `params_model` from the registry.

### Step 10: The golden gate

- [ ] **Step 10**

```bash
docker compose -p p9bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test sh -c 'timeout -s KILL 900 pytest -q tests/test_builder_port_golden.py tests/test_collection_catalog.py; echo EXIT=$?'
```

Expected: green, with `tests/fixtures/collections/golden_port.json` unchanged.
Confirm the file is untouched:

```bash
git status --short tests/fixtures/collections/golden_port.json
```

Expected: **no output**. **No default definition and no catalog preset gains a
`plex_search` in this phase** — a preset that would use one is Task 7's row to
file, not this task's code to write.

- [ ] **Step 11: Prove the registration falsifiable**

```bash
cp src/autoposter/collections/builders/__init__.py /tmp/builders_init.py.bak
```

Comment out `register(PlexSearchBuilder())`. Rerun Step 9. Expected: the four
new config tests fail with `unknown collection builder 'plex_search'`. Then:

```bash
cp /tmp/builders_init.py.bak src/autoposter/collections/builders/__init__.py
cmp /tmp/builders_init.py.bak src/autoposter/collections/builders/__init__.py && echo RESTORED
```

Rerun Step 9: green. **Paste both real outputs.**

- [ ] **Step 12: Full suite, ruff, teardown**

```bash
docker compose -p p9bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9bt4-full test sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9bt4-full
docker logs p9bt4-full | tail -5
docker compose -p p9bt4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9bt4 down
```

Expected: Task 3's count + 31, `0 failed`; `All checks passed!`.

- [ ] **Step 13: Commit**

```bash
git add src/autoposter/collections/builders/plex_search.py \
        src/autoposter/collections/builders/__init__.py \
        tests/test_builder_plex_search.py tests/test_collection_config.py \
        pyproject.toml
git commit --no-gpg-sign -m "feat(builders): plex_search

The library asked a question, answered by Plex. Emits ('plex', ratingKey) ids
so the engine resolves them through its own owned index like any other
builder's, and a filters: block on the same definition still runs client-side
-- the server narrows, the client refines, and neither is folded into the
other.

Tag values resolve against the library's own vocabulary at BUILD time, one
listFilterChoices per (library, field, libtype) per pass with the failure
memoised too (roadmap row 158, answered on this path).

Refused by name, each with Kometa's own behaviour spelled out: the implicit
base, validate:, type:, and sort: for sort_by:."
```

---

## Task 5: The live probe — READ-ONLY, and it needs the operator's server

**Files:**
- Create: `.superpowers/sdd/p9b-task-5-probe.md` (the script verbatim + the
  scrubbed results; not shipped, not in `src/`, not in `tests/`)
- Create: `.superpowers/sdd/p9b-probe-roundtrip.txt` (the raw per-attribute
  round-trip table, scrubbed)
- No code changes. If the probe finds a divergence, the FIX is a change to the
  table or the URL builder and it comes with its own oracle re-run — but that
  is a fix round, not this task's deliverable.

**This task and only this task touches a live server.** Read-only: two
`section.all()` walks, a `listFilterChoices` per attribute, and one search
query per attribute. No write, no collection creation, no library edit, no
`section.refresh()`. It is sequenced last so that Tasks 1–4 merge without it.
It may need the operator's explicit go-ahead and a quiet window — ask, and if
the answer is no or does not come, stop here and hand Task 7 the three open
probes to file as rows.

### How the probe is driven

Inside the test container, with the credentials supplied at invocation and
never written to a file:

```bash
docker compose -p p9bt5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm \
    -e PROBE_PLEX_URL="$PROBE_PLEX_URL" -e PROBE_PLEX_TOKEN="$PROBE_PLEX_TOKEN" \
    test python .superpowers/sdd/p9b_probe.py
```

`PROBE_PLEX_URL`/`PROBE_PLEX_TOKEN` are exported into the shell by the
operator (or read from the deployment's own secret store) for the duration of
the run. The script connects with
`PlexServer(os.environ["PROBE_PLEX_URL"], os.environ["PROBE_PLEX_TOKEN"])` —
the same call `src/autoposter/collections/__main__.py:60` makes — and every
line it prints goes through this scrubber before anything is recorded:

```python
def scrub(text: str) -> str:
    """Nothing leaves this script carrying the server or the token.

    Both the URL and the token appear in plexapi's own exception messages and
    in the ``_server._baseurl`` of every object, so scrubbing at the point of
    PRINTING rather than at the point of construction is the only placement
    that catches all of them.
    """
    out = str(text)
    for secret, replacement in (
        (os.environ["PROBE_PLEX_URL"], "<plex-host>"),
        (os.environ["PROBE_PLEX_TOKEN"], "<redacted>"),
    ):
        out = out.replace(secret, replacement)
    return re.sub(r"X-Plex-Token=[^&\s]+", "X-Plex-Token=<redacted>", out)
```

Delete `.superpowers/sdd/p9b_probe.py` after the run, or keep it — but it must
contain no literal URL or token either way, and the copy pasted into the report
is the authority.

### Part A — the dual-path round-trip

**Nine attributes are resident in both paths**: `year`, `resolution`,
`audience_rating`, `critic_rating`, `content_rating`, `added`, `release`,
`duration`, `studio` — 9a's nine shipped client-side filters, every one of
which is also a search attribute. (The fact sheet says eight; it is nine. If
one turns out not to be round-trippable, the report says which and why — the
likely candidate is `duration`, whose bare form v1 refuses, so its round-trip
uses `.gt`/`.lt` instead of an equality.)

For each, the two paths must agree on **the same set of rating keys**:

- **Server path:** build the query with `build_search_url` and the real
  resolver, fetch it, collect `{str(item.ratingKey)}`.
- **Client path:** walk `section.all()` once, evaluate the equivalent
  `filters:` predicate with `parse_filters` + `evaluate` +
  `PlexItemView`, collect `{str(item.ratingKey)}`.

The predicate pairs, one per attribute, written so each is decidable and none
is trivially everything or nothing:

| Attribute | Search (`params.all`) | Filter (`filters:`) |
| --- | --- | --- |
| `year` | `{"year.gte": 2010}` | `{"year.gte": 2010}` |
| `resolution` | `{"resolution": "1080"}` | `{"resolution": "1080"}` |
| `audience_rating` | `{"audience_rating.gte": 7}` | `{"audience_rating.gte": 7}` |
| `critic_rating` | `{"critic_rating.lt": 5}` | `{"critic_rating.lt": 5}` |
| `content_rating` | `{"content_rating": "PG-13"}` | `{"content_rating": "PG-13"}` |
| `added` | `{"added.after": "<a date a third of the way through the library's range>"}` | same date |
| `release` | `{"release.before": "2000-01-01"}` | same |
| `duration` | `{"duration.gt": 120}` | `{"duration.gt": 120}` |
| `studio` | `{"studio": "<a studio the library actually has, taken from the listing>"}` | same |

Record for each: `|server|`, `|client|`, `|server - client|`,
`|client - server|`, and for any non-empty difference **the titles and the
attribute values of up to five differing items**, so a divergence is
diagnosable without a second run.

**A difference is not automatically a bug**, and the report must say which of
these it is rather than picking one:
- a genuine translation error (the query means something else) — fix, re-run
  the oracle;
- a known 9a limitation (`critic_rating` missing on an item excludes it
  client-side under every operator; the server may treat absence differently);
- **the row-154 clock divergence** for `added` — the server evaluates in its
  own zone and the client in the runner's, so a same-day boundary can disagree
  by up to the offset. D6 says document, do not reconcile. If `added` diverges
  by items whose `addedAt` is within the offset of the boundary, that is the
  row-154 evidence Task 7 restates the row with — say so explicitly, with the
  offset and the item count.

### Part B — the three named probes

**Probe 1 — `show.network` as a SEARCH field.** The highest-value question in
this phase: 9a proved Plex 1.43.4 emits no `network` ITEM attribute anywhere
(0/284 shows in the listing AND absent from `/library/metadata`), which is why
the client-side filter is stranded. Whether the SEARCH FIELD answers is a
different question with a different mechanism, and if it does, the
`production_network` catalog preset unblocks.

```
1. section.listFilterChoices(field="network", libtype="show")
   -> does it return anything? how many? sample five (title, key).
2. If it does: build and fetch {"all": {"network": "<a returned title>"}}
   -> how many shows? do their studios agree with the network name?
3. Cross-check: the same shows' `studio` values from the listing.
```

Record all three. The decisive outcome is (1): an empty or absent filter field
means the search is stranded too and the preset stays GATED with a second,
stronger piece of evidence than 9a had.

**Probe 2 — language search, and the AND-under-`all` question.**

```
1. section.listFilterChoices(field="audioLanguage", libtype="movie")
   -> the full value list. Are they locale tags (es-419), 2-letter (es),
      3-letter (spa), or a mix? This decides the Task 4 Step 0 option.
2. For one code with several variants, the expansion this build produces:
   {"all": {"audio_language": "<code>"}} -> the URL, and the result count.
3. THE QUESTION: Kometa joins the expanded variants with the BLOCK's
   conjunction (builder.py:4245-4248), so under `all:` an item must carry
   EVERY variant. Compare the count from (2) against the same code under
   `any:` -- {"any": {"audio_language": "<code>"}}. If the `all:` count is
   zero or near-zero and the `any:` count is large, the expansion is
   effectively unusable under an `all:` base, which is Kometa's behaviour and
   a row to file, NOT a divergence to silently fix.
4. Same for subtitleLanguage.
```

**Probe 3 — genre search untruncation.** 9a's decisive finding was that the
section listing truncates `<Genre>` to two per item, which is why the
client-side genre filter is deferred. A search does not read the listing at
all, so it should not be affected — this probe proves it.

```
1. Pick five movies the 9a probe recorded as having 3+ genres in
   /library/metadata and 2 in the listing (or re-derive: compare
   len(item.genres) from section.all() against a /library/metadata fetch for
   the same five).
2. For the THIRD genre of each -- the one the listing drops -- run
   {"all": {"genre": "<that genre>"}} and check the item is in the result.
3. Record: 5/5, or which failed.
```

A 5/5 here is the evidence that the search path is not subject to the listing
truncation at all, which is what makes `genre` usable in a `plex_search` while
still deferred in `filters:` — and it is the concrete reason the two
vocabularies are worth keeping distinct.

### Steps

- [ ] **Step 1: Ask.** Confirm with the operator that a read-only probe may
      run, and when. If the answer is no, skip to Step 6 and write the report
      saying so.
- [ ] **Step 2: Write `.superpowers/sdd/p9b_probe.py`** — Part A's round-trip
      loop and Part B's three probes, with the `scrub` function above applied
      to every print. Nothing in it writes to Plex; add an assertion at the top
      that the only plexapi methods it calls are `all`, `fetchItems`,
      `listFilterChoices` and `fetchItem`, listed in a comment, and grep the
      finished script for `edit(`, `addLabel`, `removeLabel`, `upload`,
      `delete`, `refresh` and paste the (empty) grep output into the report.
- [ ] **Step 3: Run it.** The command above. Expected: a table for Part A and
      three blocks for Part B, none containing the host or the token.
- [ ] **Step 4: Verify the scrub.** Grep the captured output for the host and
      for `X-Plex-Token`:

      ```bash
      grep -c "$PROBE_PLEX_URL" .superpowers/sdd/p9b-probe-roundtrip.txt
      grep -c "X-Plex-Token=[^<]" .superpowers/sdd/p9b-probe-roundtrip.txt
      ```

      Expected: `0` from both.
- [ ] **Step 5: Write `.superpowers/sdd/p9b-task-5-probe.md`** — the script
      verbatim, the raw output verbatim, and a verdict paragraph per probe
      naming what it unblocks, what it strands, and what it files.
- [ ] **Step 6: Full suite, ruff, teardown, commit.**

      ```bash
      docker compose -p p9bt5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
          run --rm -d --name p9bt5-full test sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
      docker wait p9bt5-full
      docker logs p9bt5-full | tail -5
      docker compose -p p9bt5 -f docker-compose.yml -f .superpowers/isolated-db.yml \
          run --rm test ruff check src tests
      docker compose -p p9bt5 down
      git add .superpowers/sdd/p9b-task-5-probe.md .superpowers/sdd/p9b-probe-roundtrip.txt
      git commit --no-gpg-sign -m "docs(9b): the read-only live probe

      Nine dual-resident attributes round-tripped server-path against
      client-path, plus the three open probes: show.network as a search field,
      the language expansion's AND-under-all, and whether the search path is
      subject to the listing's genre truncation.

      Read-only throughout; the server address and token are scrubbed from
      every recorded line."
      ```

---

## Task 6: The per-family tail — FILED, NOT EXECUTED

**This task is not part of this plan's execution.** It is written out here so
that Task 7 can file it accurately and so a reviewer can see exactly what v1
leaves undone, family by family, against the same table. Each family is one
follow-up PR against `src/autoposter/collections/filters.py` plus, where noted,
one new mechanism.

v1 ships **19** of Kometa's 55 non-music search attributes. The remaining
**36**, grouped by what each family needs beyond a table row:

| Family | Attributes | Count | Needs beyond a row |
| --- | --- | --- | --- |
| **A. People tags** | `actor`, `director`, `producer`, `writer` | 4 | `get_actor_id` (`plex.py:1273-1280`): a person not in the library's tag list is looked up by `hubSearch` and matched on `Role` + section id. A second lookup mechanism, and a second failure mode. `director`/`producer`/`writer` are movie-only (`plex.py:431-436`). |
| **B. Text** | `title`, `edition` | 2 | Nothing — six string modifiers each, the `studio` row is the worked example. `edition` is dual-listed (string AND tag) like `studio`. |
| **C. Year specials** | `decade`, plus `year: current_year[-N]` | 1 + a value grammar | `decade` has no `.not` (`no_not_mods`), and the `current_year` words (`builder.py:4333-4348`) are a relative value grammar on an int, resolved at build time — the same "resolved against the run, not the parse" property `_Today` already has. |
| **D. Media booleans** | `hdr`, `dovi`, `trash`, `duplicate`, `unmatched` | 5 | Nothing — the `bool` type and its renderer ship in v1. `duplicate` is movie-only; `hdr`/`dovi`/`trash` re-scope to the episode libtype on a show library. |
| **E. Show sub-libtypes** | `season_collection`, `season_label`, `episode_collection`, `episode_label`, `episode_title`, `episode_actor`, `episode_added`, `episode_air_date`, `episode_last_played`, `episode_plays`, `episode_user_rating`, `episode_critic_rating`, `episode_audience_rating`, `episode_year`, `episode_unplayed`, `episode_duplicate`, `episode_progress`, `episode_unmatched`, `show_unmatched`, `unplayed_episodes` | 20 | The biggest family and the only one needing an ENGINE change: several are only meaningful with `type: season`/`type: episode`, which v1 refuses — and the season/episode `sort_types` and sort matrices (`plex.py:668-715`) come with them. `builder_level` collections are roadmap row 88. |
| **F. Other tags** | `country` | 1 | Nothing. |
| **G. Account rating** | `user_rating` | 1 | Nothing mechanically; it is per-account like `plays`, and the row's note must say so. |
| **H. Folder** | `folder_location` | 1 | `listFilters` at run time to discover the field name, which is not fixed (`get_search_key`, `plex.py:1286-1297`, and it re-scopes to the episode libtype on shows). **Deferred by name in D3.** |
| **I. Codec** | `audio_codec` | 1 | A string attribute Kometa also lists as track-only; whether a movie library answers it is a probe. |

Plus three strands that are not attributes:

- **The search-regex vocabulary expansion** (D5). `studio.regex` in Kometa is
  not a regex sent to Plex: it matches the pattern against the library's tag
  list and sends the matching KEYS (`builder.py:4301-4323`). Shipping it means
  building that expansion, and it is a real feature — "every genre ending in
  -core" — just not the same mechanism as `filters:`' `.regex`.
- **Tag `.count_gt`/`.count_gte`/`.count_lt`/`.count_lte`** — Kometa's filter
  side has them (`builder.py:419`); the search side does not, so this is a
  `filters:` row, not a search one.
- **`type: season` / `type: episode`** — the libtype selector v1 refuses, which
  family E depends on.

---

## Task 7: Wrap — the honest roadmap, the catalog, and the new rows

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`
- Modify: `src/autoposter/collections/catalog.py`
- Test: `tests/test_collection_catalog.py` (only if a preset's readiness changes)

- [ ] **Step 1: Row 101 — closed, with its scope stated**

Append to row 101 (`docs/.../2026-08-22-full-parity-roadmap.md:203`), in the
`**answered 9b:**` style row 96 already uses:

> **answered 9b (v1):** delivered — `plex_search` is a registered builder
> (`src/autoposter/collections/builders/plex_search.py`) whose query string is
> byte-identical to the one Kometa 2.4.8's own `build_filter` produces for the
> same config, proven by thirteen pinned golden URIs
> (`tests/test_collection_search_oracle.py`). **19 of Kometa's 55 non-music
> search attributes ship**, not all of them: the 9a nine as searches
> (`year`, `resolution`, `audience_rating`, `critic_rating`, `content_rating`,
> `added`, `release`, `duration`, `studio`), the six row-155 strandeds
> (`genre`, `label`, `collection`, `audio_language`, `subtitle_language`,
> `network`) — which a SEARCH can reach even though the section listing cannot
> — and the four watch-state families (`unplayed`, `progress`, `plays`,
> `last_played`). The remaining 36 are the per-family tail, filed as rows
> <N..N+8> below. Sorts ship complete for both libtypes (31 movie / 29 show,
> `src/autoposter/collections/search_sorts.py`), pre-encoded and verbatim;
> season/episode/music sorts wait on the libtype selector. Refused
> deliberately, each with the Kometa behaviour spelled out in the message: the
> implicit base and the `.and` suffix (one key would mean two memberships),
> `validate: false` (it builds a query missing the clause it could not
> resolve), search `.regex` (Kometa's is a client-side vocabulary expansion,
> not a regex Plex sees), and a bare `duration:` (Kometa's search vocabulary
> has no blank-modifier form for a float attribute, so Kometa refuses the same
> key from the same list).

- [ ] **Step 2: Row 96 — the arithmetic correction**

Row 96 (`:198`) currently hands "the rest of ~60 attributes" to 9b, and
`src/autoposter/collections/catalog.py:1341` says "the ~45-attribute residue
that row hands to 9b". Both are wrong and the correction is the honest part of
this wrap. Append to row 96:

> **9b's correction to this row's arithmetic, and it is not a rounding
> difference.** Kometa's FILTER vocabulary is **70** distinct names
> (`modules/builder.py:270-350`, `filters_by_type`), of which this table covers
> **15**, so the residue is **55** and not "~45". More importantly, 9b does NOT
> close it, and could not: the filter and search vocabularies **are not
> nested**. **26** names are in both; **44** are filter-only with no Plex
> search field at all (`aspect`, `height`, `width`, `channels`, `versions`,
> `filepath`, `folder`, `summary`, the five `has_*` booleans, the `tmdb_*`/
> `tvdb_*`/`imdb_*` families, `seasons`/`episodes`/`albums`/`tracks`,
> `history`, `original_language`, `origin_country`, `record_label`,
> `season_title`, `show_title`, the codec/profile family, the two track-title
> filters, `stinger_rating`, and the three `*_episode_aired` dates); **29** are
> search-only with no client-side filter (`decade`, `dovi`, `hdr`, `trash`,
> `duplicate`, `unmatched`, `unplayed`, `progress`, `unplayed_episodes`,
> `show_unmatched`, `folder_location`, `season_collection`, `season_label`, and
> the sixteen `episode_*`). So "9b closes the rest of 96" was true only of the
> overlap. What 9b did do for this row: it added `plays` and `last_played` to
> the table (both ARE Kometa filters, contrary to the phase's own fact sheet)
> under a new source tier `unprobed`, because 9a probed seven attributes and
> these were not among them and a refusal citing a probe verdict that does not
> exist would be worse than the gap. Closing 96's remainder is a client-side
> job — a metadata-prefetch budget for the readable-but-not-free families
> (row 155a) plus a row per filter-only attribute — and it is not this phase's.

- [ ] **Step 3: Rows 154, 157, 158 — restate, with evidence**

**Row 154** (`:250`). Append (D6: document the divergence, do not reconcile):

> **9b widens it again, in the other direction.** A `plex_search` date
> predicate is evaluated by the PLEX SERVER, in the server's clock; a
> `filters:` date predicate is evaluated by the runner, in the runner's. So the
> same definition can now carry two date predicates that disagree about the
> same item, and neither is wrong — they are answering in different zones. Not
> reconciled, deliberately: the fix is not "convert to UTC" (the value that
> would make `added` mean what an operator reads it as is the Plex server's own
> zone, and the listing does not carry it), and forcing one side to the other's
> clock would make a collection depend on where the pass ran. What 9b did
> instead is document it in both places
> (`src/autoposter/collections/builders/plex_search.py`'s module docstring and
> `PlexSearchParams`' docstring) and recommend the relative-window forms
> (`added: 30`, `last_played.not: 6o`) for search dates, which are day-granular
> or coarser and therefore insensitive to any offset short of a day.
> **Evidence:** Task 5's round-trip, `added` row — record here the observed
> `|server - client|` and `|client - server|` counts and whether the differing
> items' `addedAt` values fall within the offset of the boundary. *(If Task 5
> did not run, say so here and leave the row open with that stated.)*

**Row 157** (`:253`). It stays as 9a left it — D7: no new operator names in
9b. Append one sentence:

> **9b confirms the shape rather than changing it.** `plex_search` ships
> `.before`/`.after` (absolute, strict, exactly as Plex answers them) and the
> bare/`.not` relative windows, and adds no new operator name — so whatever
> spelling this row eventually chooses (`release.from`/`release.to`, or
> `release.on_or_after`) has to be added to both blocks at once, or it becomes
> the same-name-different-filter defect one level along. Revisit on demand.

**Row 158** (`:254`). Append:

> **9b answers half of it, and the half it answers is the one with a server.**
> A `plex_search` sends Plex a KEY, never a written word, so resolving the
> value against the library's own vocabulary is not a check bolted on — it is
> how the query gets built. `src/autoposter/collections/builders/plex_search.py`
> does it at BUILD time (never load: a definition with no `libraries:` key runs
> against every library in the pass, the same argument
> `require_library_type` makes), one `listFilterChoices` per (library, field,
> libtype) per pass memoised in `run_cache` **including the failure**, matching
> on Kometa's four spellings per choice (`title`, `key`, and both lowercased,
> `modules/plex.py:1308-1315`). A misspelled value is a refusal naming the
> value and the attribute, which is Kometa's behaviour
> (`builder.py:4433-4437`). The `filters:` side is unchanged and this row stays
> open for it: a client-side filter has no key to resolve, so a typo there
> still produces an empty collection rather than a refusal, and the fix there is
> a lookup added for validation's sake alone — which is the per-library,
> per-attribute Plex call 9a declined to add speculatively. The cost is now
> known rather than guessed: it is exactly the call this builder already makes.

- [ ] **Step 4: The catalog**

**The `media_aspect` copy fix**
(`src/autoposter/collections/catalog.py:1331-1347`). Its `gated_row` citation
is right in letter — `aspect` really is in the filter residue row 96 hands on —
and its copy is wrong twice: the residue is 55 and not "~45", and it implies 9b
will supply the attribute. It will not: `aspect` is one of the 44 filter-only
names with **no Plex search field at all**, so no search can reach it and the
row waits on a client-side metadata budget. Replace the description with:

```python
        description=(
            "Eight collections, one per aspect ratio Kometa names -- 1.33 "
            "Academy Aperture through 2.77 Cinerama. The values are a fixed "
            "list and would need no enumeration; what is missing is the "
            "attribute. Row %d shipped its tier-1 half, and `aspect` is not "
            "one of the tier-1 rows in collections/filters.py -- it sits in "
            "the 55-attribute filter residue that row carries. Note what 9b "
            "did NOT do for it: `aspect` is one of the 44 names in Kometa's "
            "filter vocabulary with no Plex SEARCH field at all, so the "
            "plex_search builder cannot reach it either. This waits on a "
            "client-side metadata budget, not on a query language."
            % FILTER_TIER_TWO_ROW
        ),
```

**Rows Task 5 may unblock — and only if it proved them.** `media_audio_language`
(`:1349-1366`), `media_subtitle_language` (`:1368-1382`) and
`production_network` are the three this phase could touch. The rule:

- If probe 2 showed the language search returns a usable set — and note the
  `all:`-versus-`any:` finding, because a preset built on an `all:` base with an
  expanding code would build empty collections — the two language presets may
  move from GATED to ready **in a follow-up PR of their own**, not here.
- If probe 1 showed `network` has no search filter field either,
  `production_network` stays GATED and its description gains the second,
  stronger piece of evidence.
- If Task 5 did not run, **nothing moves.** All three stay GATED and Task 7
  says so, in the row.

**No preset gains a `plex_search` in this phase either way.** The golden gate
holds: `tests/fixtures/collections/golden_port.json` is byte-identical at the
end of this branch, and `git status --short tests/fixtures/collections/` prints
nothing.

The `DYNAMIC_ENGINE_ROW` presets (`:795`, `:813`, `:1279`) stay gated on 10a
regardless — a query language does not give them the per-value enumeration
they need.

- [ ] **Step 5: The new rows**

File each in the roadmap's row table, in the same column shape as its
neighbours (`| N | title | body | size | verification | deps |`):

1. **The per-family search tail** — nine rows, one per family in Task 6's
   table (A People, B Text, C Year specials, D Media booleans, E Show
   sub-libtypes, F country, G user_rating, H folder_location, I audio_codec),
   each naming its attributes, its count, and what it needs beyond a table row.
   Deps: `101`.
2. **Search regex as a vocabulary expansion** — D5's deferral. Kometa's
   `studio.regex` matches the library's tag list and sends the KEYS
   (`builder.py:4301-4323`); shipping it is a real feature under a spelling
   that must not collide with `filters:`' client-side `.regex`. Deps: `101`.
3. **The libtype selector (`type: season`/`type: episode`)** — what family E
   depends on, and what brings the season/episode sort matrices
   (`plex.py:668-715`) with it. Deps: `101`, `88`.
4. **`plays`/`last_played` were never probed** — the `unprobed` source tier
   exists for exactly two rows, and one `section.all()` walk answers whether
   `viewCount`/`lastViewedAt` reach the listing. Note also that both are
   PER-ACCOUNT, which no other row in the table is, so "shipping" them
   client-side needs a sentence about whose token the pass runs with. Deps:
   `96`.
5. **Query folding, deliberately not built** — D2(b). A `filters:` block on a
   `plex_search` definition stays client-side; folding the overlap into the
   query would be an optimisation whose correctness surface includes two
   vocabularies that are not the same set and two clocks that are not the same
   clock. Filed so it is not re-litigated by whoever notices the redundancy.
   Deps: `101`, `96`.
6. **Anything Task 5 stranded** — one row per open probe that came back
   negative or did not run, each carrying the probe's own numbers.

- [ ] **Step 6: Row 119 tally**

If any run in Tasks 1–5 hit a wall-clock flake (a test green on rerun with no
edit), append the instance to row 119 (`:221`) in the existing `**9b tally
(2026-08-26):**` style, naming the test and what drifted. If none did, add
nothing — an empty tally line is noise.

- [ ] **Step 7: Whole-branch review**

Request a review with the most capable model available, scoped to:
- the table's transcription fidelity against the fetched Kometa v2.4.8 files
  (the reviewer re-fetches; a recollection is what the 9a `SETTLED-BY-REVIEW`
  marker got wrong);
- the oracle's isolation — that `.superpowers/oracle/9b/kometa_build_filter.py`
  imports nothing from this repository, and that every removal from Kometa's
  code is marked and defensible;
- the refusal messages: does each one say what Kometa does and what to write
  instead;
- the build-time tag lookup's call count and its failure memoisation;
- secrets hygiene in Task 5's recorded output.

One batched fix round. Every fix syncs this plan document in the same commit.

- [ ] **Step 8: Both suites, ruff, PR**

```bash
docker compose -p p9bt7 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm -d --name p9bt7-full test sh -c 'timeout -s KILL 1800 pytest -q; echo EXIT=$?'
docker wait p9bt7-full
docker logs p9bt7-full | tail -5
docker compose -p p9bt7 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm test ruff check src tests
docker compose -p p9bt7 down
git status --short tests/fixtures/collections/
```

Expected: full suite green at Task 4's count (Tasks 5 and 7 add no tests
unless a probe finding forces a fix); `All checks passed!`; **no output** from
the `git status`.

Open the PR from `feat/plex-search-dsl`. If it is stacked (see *Branch and cut
point*), the description opens with the retarget instruction. No AI
attribution anywhere in it.

---

## Self-review

Run against the fact sheet (`.superpowers/sdd/p9b-facts.md`) and the roadmap's
9b section.

**Spec coverage.**

| Requirement | Task |
| --- | --- |
| D1 — any/all + Kometa-verbatim modifiers; refuse the implicit base, `.and`, `validate: false` | T1 (`.and`), T4 (base, `validate`) |
| D2a — the builder emits `("plex", ratingKey)` ids | T4 |
| D2b — `filters:` stays client-side, no query folding | T4 (module docstring + config test), T7 (filed row 5) |
| D2c — one table, `searchable`/`filterable` columns, cross-referencing refusals | T1 |
| D2d — `sort_by`+`limit` in params vs `sort`+`limit` on the definition | T2 (the composition table), T4 (params docstring) |
| D3 — the nineteen v1 attributes; `folder_location` deferred by name | T1 (rows), T6 (H) |
| D4 — build-time tag validation, run-cached, Kometa's four keys | T4 |
| D5 — search `.regex` refused, with the reason | T1 |
| D6 — the date divergence documented in both places, windows recommended | T4 (both docstrings), T5 (evidence), T7 (row 154) |
| D7 — no new operator names | T1 (the operator sets), T7 (row 157) |
| Modifier map keyed `(type, operator)` + structural collision test | T1 |
| `FILTER_ATTRIBUTES` extended IN PLACE, checksums | T1 |
| Sorts pre-encoded verbatim, `SORT_TYPES`, `sort_by` naming | T2 |
| URL assembly: conjunctions, push/pop, ×60000, `o`→`mon`, `.rated`, booleans, per-value joins, string quoting, tag KEYS | T3 |
| `includeCollections` footgun test | T3 |
| The 3213-3220 unknown-key shorthand NOT reproduced | T4 (`extra="forbid"` + the named refusals) |
| Language base-ISO expansion transcribed and tested | T4 (Step 0 decision, `_language_keys`), T5 (probe 2) |
| The oracle: standalone driver, N configs, pinned data | T3 (thirteen) |
| The live probe: dual-path round-trip + three named probes, read-only, scrubbed | T5 |
| The per-family tail enumerated | T6 |
| Wrap: rows 101/96/154/157/158, `media_aspect` copy, catalog honesty, new rows | T7 |
| Container-only testing, unique `-p`, teardown without `-v`, the docker-wait recipe | Global Constraints |
| Full repo-relative paths; row 119; async-SQLAlchemy rule; secrets/URL hygiene; oracle provenance; purity; golden gate; pre-authorized fixes; live plan sync | Global Constraints |

**Placeholder scan.** No `TBD`, no "implement later", no "similar to Task N",
no "add appropriate error handling". Two derivations are deliberately produced
at execution rather than written here, and both name the exact command that
produces them and the exact place the result is pasted: the oracle's thirteen
golden strings (T3 Step 7 → Step 8, with six of them hand-derived in the plan
as a cross-check) and Task 5's probe output. That is the 9a oracle's own
procedure — fetch the code, run it, pin the answer — not a gap.

**Type consistency.** `parse_filters(raw, *, field, searching, base)` is used
with that exact signature in T3 and T4. `build_search_url(group, *, libtype,
sort_by, limit, resolve_tag)` likewise. `resolve_tag` returns
`tuple[str, ...]` everywhere — in T3's `TagResolver` protocol, in T3's test
`resolve`, in the oracle test's `resolve`, and in T4's `_Resolver.__call__`.
`FilterAttribute.field_for(libtype)` is defined in T1 and used in T3 and T4.
`SORT_TYPES[libtype].key` in T2 and T3. `search_kinds` is checked in T3's
`_render_predicate` (not in T4), so there is one place it can be wrong.
`RelativeWindow(count, unit)` is constructed in T1 and consumed in T3 only.
`SEARCH_MODIFIERS` is read in T3 only. `inline` is set in T1's `_parse_nested`
and read in T3's `_render_group` only.

**One thing this review flags rather than fixes.** T1 Step 4 says "existing
note, unchanged" for the fifteen rows' `note` text. That is a deliberate
instruction, not an elision: those notes are 9a's adjudicated record and
rewriting them would destroy provenance. The new *columns* carry their own
comments where they need one, and three rows (`resolution`, `duration`,
`studio`, `network`) get an added comment above the new arguments explaining
why the search column differs from the client one.
