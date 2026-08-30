# Phase B — Batched Plex Read, Tier-2 Accessors, Credits Cache — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Plex-side batched metadata read (`/library/metadata/{k1,k2,…}`), a deliberate second accessor tier fed by it, the many-to-many credits cache and its scan, and the six roadmap rows that unblock on them (197 parent; 155, 169, 175, 180, 91, 194, 198).

**Architecture:** One read-only probe task (T1) answers five open questions against the production server and writes a decision table; T2 builds the chunked multi-key fetch (plain data out, thread-confined, `ceil(N/chunk)` calls pinned by test) plus a per-run enrichment cache scoped to resolved item sets; T3 opens the tier-2 accessor branch (five attributes, explicit and refusing-when-absent, per adjudication C3); T4–T7 are written in FULL below but are **CONFIRMED-BY-T1** — the controller re-validates each against T1's report before dispatch (adjudication C2/C8). The credits cache is a dedicated composite-PK mapping table (adjudication C4), never a widening of `item_facts`.

**Tech Stack:** Python 3.13/3.14, plexapi 4.18.2 (`fetchItems` already translates a list of ints to `/library/metadata/{k1,k2,…}` — `base.py:334-335`), SQLAlchemy async + Alembic + Postgres, pytest in the compose test container.

**Binding requirements:** `.superpowers/sdd/p-phaseb-facts.md` (C1–C8, settled). Where this plan and that file could ever disagree, that file wins.

## Global Constraints

Every task's requirements implicitly include this section.

1. **The reload-trap law.** Never a per-item Plex reload. `PlexPartialObject.__getattribute__` issues a synchronous `_reload()` GET whenever an attribute reads back `None` or `[]` (`adopt/walk.py:168-176` — that docstring is law; `filter_values.py:22-27` makes the rule structural with `object.__getattribute__`). Every new read of a plexapi attribute in this phase goes through `object.__getattribute__` or reads an attribute that is never falsy (`ratingKey`). The tier-1 rule "reading a filter value never costs a Plex request" (`filter_values.py:8`) stays absolute for tier-1 reads; tier 2 is a second, DELIBERATE tier beside it (facts C3), never an exception carved through it.
2. **Refusals never become empty memberships.** An item absent from the enrichment (batch failure, chunk refused, key gone) REFUSES the definition rather than evaluating with silently-missing values (facts C3). A credits-cache refusal is never cached; an attempt is stamped on the ITEM whether or not it found anything; no row means ABSENT from every query — never a bucket, never a zero (facts C4, `facts_enumeration.py:17-24` verbatim).
3. **Container discipline (the standing recipe).** Unique compose project per task (`pphb1` … `pphb7`), always with the `.superpowers/isolated-db.yml` overlay. Tee output to a path under `/app/.superpowers/` inside the container — never rely on streamed stdout (it is filtered, and `--rm` deletes the container before it can be re-read). A long run (the full suite) uses **no `--rm`**, is started detached with `run -d --name <project>-full`, and is waited on with a foreground `docker wait`; the log is then read from the host. Teardown is `docker compose -p <project> down` — **never** `down -v`. Two pytest commands against the same compose project are unsafe — keep unique `-p` names. Execution is main-tree (no other phase runs in parallel — facts C8); if a worktree is nonetheless used, any `run-*.log` written inside it is copied out BEFORE the worktree is pruned.
4. **Probe discipline.** Probes are read-only GETs against the production server, run as `kubectl exec -n media -i deploy/autoposter -- python - < script.py` (the 9a/9b/10a convention). Scripts contain no literal URL and no literal token: the Plex URL comes from the operator config (`load_config(DEFAULT_CONFIG_PATH).plex.url`) and the token from `Secrets.from_env().plex_token` — **never from the repo's `.env`**. Every script scrubs its own output (host and token replaced before printing); probes never print secrets; the key-grep over the captured report is shown in the report; scripts are deleted after their output is captured.
5. **No URLs/tokens in tracked files.** No operator URL, host, or token in code, comments, tests, fixtures, logs, reports or the PR body. Plex API *paths* (`/library/metadata/{k1,k2}`) are fine and are the point.
6. **Commits.** Conventional messages, `--no-gpg-sign`, staged **by name** (never `git add -A`). No `Co-Authored-By`, no AI attribution of any kind, in commits or the PR body.
7. **Branch.** `feat/phase-b-plex-read`, cut from `origin/main` after a fetch, verified with a **content probe** (never a sha probe): `grep -q '^| 198 |' docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` must succeed on the cut branch (row 198 was Phase A's wrap and is the newest content this phase depends on).
8. **Artifacts.** Phase artifacts under `.superpowers/sdd/` use the `p-phaseb-` prefix. The PR body goes to `.superpowers/sdd/p-phaseb-pr-body.md` — `.superpowers/` is gitignored and the file stays uncommitted. **Push and PR creation are NOT in this plan**: the controller does both after the whole-branch review.
9. **Golden gate byte-identical.** `tests/fixtures/collections/golden_port.json` must not change in any task — assert with a real run in every task that touches `src/`, never by assuming it.
10. **Every refusal names the way out.** A message that says only what is wrong is half a message.
11. **CONFIRMED-BY-T1 semantics (facts C2/C8).** Tasks 4–7 are written in full below and each opens with a `CONFIRMED-BY-T1` block naming the decision-table rows it depends on. The controller re-validates those rows against T1's committed report (`docs/research/plex-batch-probe/README.md` §Decisions) before dispatching the task. If T1's probe (b) finds NO credit tags in the batch response, T4 and T5 are **BLOCKED-FOR-ADJUDICATION** — the honest outcomes (a slower drift-paced scan on the 500/week shape, a TMDb-side credits source resolving row 194's open question toward filmography semantics, or a re-file) are the controller's call, not this plan's. Honesty over momentum. User is AFK: a BLOCKED state PARKS that task and execution continues with unblocked ones.

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `docs/research/plex-batch-probe/README.md` | **Create.** The five probes' scrubbed captured output + the §Decisions table (D1–D7) that gates tasks 4–7 | T1 |
| `src/autoposter/plex/client.py` | **Modify.** `TAG_BATCH_CHUNK`, `ItemTags`, `fetch_tag_index()` and the shared `_iter_metadata_batches()` (T2); `CreditTags`, `fetch_credit_index()` (T4) | T2, T4 |
| `src/autoposter/collections/enrichment.py` | **Create.** The per-run enrichment cache: `ensure_tags()` over `run_cache`, failure memoised, misses memoised, scoped to asked-for keys only | T2 |
| `src/autoposter/collections/filters.py` | **Modify.** `SOURCE_TIERS` grows `"tier2-batched"`; five rows move tiers (T3); `batched_attributes()` helper (T3); four people rows (T5); `user_rating` row + `plays`/`last_played` verdicts (T7) | T3, T5, T7 |
| `src/autoposter/collections/filter_values.py` | **Modify.** `BATCHED_ATTRIBUTES`, `EnrichmentNotLoaded`, `PlexItemView(item, tags=None)` with the batched accessor branch; docstring updated (six deferred → one stranded) | T3 |
| `src/autoposter/config/schema.py` | **Modify.** `_filters_must_parse_and_be_readable` admits `tier2-batched` rows; `network`-specific refusal copy (T3); `SchedulerConfig.credits_scan_days` (T4) | T3, T4 |
| `src/autoposter/collections/engine.py` | **Modify.** `_run_one` gains the enrichment pre-step; `_passing(definition, items, library, tags=None)` | T3 |
| `src/autoposter/db/models.py` | **Modify.** `ItemCredit` (composite PK, `ImdbEpisode` mold), `MediaItem.credits_attempted_at` | T4 |
| `alembic/versions/<rev>_item_credits.py` | **Create.** The migration (hand-written, `c1a7f30b9e42` mold) | T4 |
| `src/autoposter/collections/credits.py` | **Create.** `scan_credits`/`scan_library_credits`, `enumerate_credits` (GROUP BY counts), `credits_coverage` | T4 |
| `src/autoposter/scheduler/jobs.py` | **Modify.** `make_credits_job(holder, server_factory)` | T4 |
| `src/autoposter/app.py` | **Modify.** Register the credits job beside `make_drift_job` (`:294`) | T4 |
| `src/autoposter/collections/builders/credits_family.py` | **Create.** `CreditsFamilyBuilder` (smart family, `DynamicBuilder` mold, enumerated from `item_credits`) | T5 |
| `src/autoposter/collections/builders/__init__.py` | **Modify.** Register `CreditsFamilyBuilder` (T5), `PlexCollectionlessBuilder` (T6) | T5, T6 |
| `src/autoposter/collections/catalog.py` | **Modify.** The four `people_top_*` packs flip GATED→READY with `credits_family` collections (T5); row-comment bookkeeping (T7) | T5, T7 |
| `src/autoposter/collections/builders/collectionless.py` | **Create.** `PlexCollectionlessBuilder` — row 91 | T6 |
| `src/autoposter/collections/smart.py` | **Modify.** `count_matches` becomes a container-size-0 read (gated on D5) | T7 |
| `src/autoposter/collections/dynamic_types.py` | **Modify.** Docstring: the people-gating paragraph re-pointed at what shipped | T7 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modify.** Rows 197/155/169/175/180/91/194/198 closed/re-pointed per facts C7, append/in-row only | T7 |
| `tests/test_plex_tag_batch.py` | **Create.** The call-count pin (`ceil(N/chunk)` exactly), chunk cap, plain-data, missing-key behaviour | T2 |
| `tests/test_collection_enrichment.py` | **Create.** `ensure_tags`: cache hit, only-missing fetch, failure memo, miss memo | T2 |
| `tests/test_collection_filter_values.py` | **Modify.** Batched accessors, `EnrichmentNotLoaded`, the accessor-map-matches-tiers pins | T3 |
| `tests/test_collection_filters.py` | **Modify.** Table checksums per task (arithmetic given inline in each task) | T3, T5, T7 |
| `tests/test_collection_config.py` | **Modify.** `genre:` filter now LOADS; `network:` refusal copy pinned | T3 |
| `tests/test_builder_engine.py` | **Modify.** Engine enrichment wiring: scoped fetch, refusal on missing item | T3 |
| `tests/test_collection_credits.py` | **Create.** Scan, attempt stamp, missing-value rule, enumeration counts, coverage | T4 |
| `tests/test_builder_credits_family.py` | **Create.** depth/limit narrowing, fan-out refusal, sweep record, per-key containment | T5 |
| `tests/test_builder_collectionless.py` | **Create.** Membership, exclusions, call count, raise-on-missing | T6 |
| `tests/test_collection_smart.py` | **Modify.** `count_matches` container-size read (gated on D5) | T7 |
| `tests/test_collection_catalog.py` | **Modify.** Pack readiness flips | T5 |
| `tests/test_scheduler_jobs.py` (or the existing drift-job test module) | **Modify.** The credits job's cadence + run wiring | T4 |

---

### Task 1: The five probes and the decision table

**Files:**
- Create: `docs/research/plex-batch-probe/README.md`
- Create (transient, deleted in-task): `probe_a.py` … `probe_e.py` in the working directory
- Commit: `docs/superpowers/plans/2026-08-30-phase-b-plex-read.md` (this plan)

**Interfaces:**
- Produces: the §Decisions table D1–D7 that Tasks 2–7's `CONFIRMED-BY-T1` blocks cite. Later tasks rely on the exact row names `D1`…`D7` below.

Read-only throughout. Every script follows the same preamble: config-sourced URL and env-sourced token, a scrub function, and a connect wrapped so no traceback can quote the tokenised URL (the 10a probe's precaution, `docs/research/plex-dynamic-probe/README.md`).

- [ ] **Step 1: Cut the branch and commit this plan**

```bash
git fetch origin main
git checkout -b feat/phase-b-plex-read origin/main
grep -q '^| 198 |' docs/superpowers/specs/2026-08-22-full-parity-roadmap.md && echo CONTENT-PROBE-OK
git add docs/superpowers/plans/2026-08-30-phase-b-plex-read.md
git commit --no-gpg-sign -m "docs(plan): phase B — batched Plex read, tier-2 accessors, credits cache"
```

Expected: `CONTENT-PROBE-OK`, then one commit containing only the plan.

- [ ] **Step 2: Write the shared preamble and the five probe scripts**

Every script starts with this exact preamble (shown once here; repeat it verbatim at the top of each script):

```python
import sys, time
from urllib.parse import urlsplit

from plexapi.server import PlexServer

from autoposter.config.loader import DEFAULT_CONFIG_PATH, load_config
from autoposter.config.schema import Secrets

_config = load_config(DEFAULT_CONFIG_PATH)
_url = _config.plex.url
_token = Secrets.from_env().plex_token
_host = urlsplit(_url).netloc

def scrub(text):
    return str(text).replace(_url, "<plex-url>").replace(_host, "<plex-host>").replace(_token, "<token>")

def say(*parts):
    print(" ".join(scrub(p) for p in parts), flush=True)

try:
    server = PlexServer(_url, _token)
except Exception as error:  # class name only -- the message can quote the URL
    print("UNREACHABLE (%s) -- probe BLOCKED" % type(error).__name__)
    sys.exit(1)

def section_of(kind):
    for s in server.library.sections():
        if s.type == kind and s.title not in ("DVR",):
            return s
    raise SystemExit("no %s section" % kind)

def keys_of(section):
    # raw listing, one GET, attribs only -- never a plexapi partial-object read
    data = server.query("/library/sections/%s/all" % section.key)
    return [int(el.attrib["ratingKey"]) for el in data if "ratingKey" in el.attrib]
```

**`probe_a.py` — the batched read on the SHOW library (never measured; 284 shows):**

```python
# ... preamble ...
TAGS = ("Genre", "Label", "Collection", "Country", "Role", "Director", "Writer", "Producer", "Stream")

show = section_of("show")
keys = keys_of(show)
say("show rating keys:", len(keys))

def tag_counts(items):
    counts = dict.fromkeys(TAGS, 0)
    for item in items:
        for el in item._data.iter():
            if el.tag in counts:
                counts[el.tag] += 1
    return counts

for chunk in (50, 100, 200):
    batch = keys[:chunk]
    start = time.monotonic()
    items = show.fetchItems(batch)
    elapsed = time.monotonic() - start
    say("chunk=%3d returned=%3d %6.2fs tags=%s" % (chunk, len(items), elapsed, tag_counts(items)))

# stream language attribs, for T3's accessor spelling (D6): sample the first
# batched show is wrong -- shows carry no Media; sample 5 MOVIES instead
movie = section_of("movie")
mkeys = keys_of(movie)[:5]
for item in movie.fetchItems(mkeys):
    for el in item._data.iter("Stream"):
        if el.attrib.get("streamType") in ("2", "3"):
            say("  stream type=%s language=%r languageCode=%r languageTag=%r"
                % (el.attrib.get("streamType"), el.attrib.get("language"),
                   el.attrib.get("languageCode"), el.attrib.get("languageTag")))
    break
```

**`probe_b.py` — do credit tags ride the batch at all? (GATES C4 — the highest-value question this phase asks):**

```python
# ... preamble ...
CREDITS = ("Role", "Director", "Writer", "Producer")

for kind in ("movie", "show"):
    section = section_of(kind)
    keys = keys_of(section)
    batch = keys[:200]
    items = section.fetchItems(batch)
    totals = dict.fromkeys(CREDITS, 0)
    role_histogram = {}
    for item in items:
        n = 0
        for el in item._data.iter():
            if el.tag in totals:
                totals[el.tag] += 1
                if el.tag == "Role":
                    n += 1
        role_histogram[n] = role_histogram.get(n, 0) + 1
    say(kind, "batch of", len(items), "credit tag totals:", totals)
    say(kind, "Role-per-item histogram:", dict(sorted(role_histogram.items())))

    # single-key metadata vs batch, credit families only, 3 items
    for key in keys[:3]:
        single = section.fetchItems([key])[0]
        def credits_of(it):
            out = {}
            for el in it._data.iter():
                if el.tag in CREDITS:
                    out.setdefault(el.tag, []).append(el.attrib.get("tag"))
            return out
        s, b = credits_of(single), credits_of(next(i for i in items if int(i.ratingKey) == key))
        say("  key=%s MATCH=%s single=%s" % (key, s == b, {k: len(v) for k, v in s.items()}))

    # D7: can listFilterChoices enumerate the people fields on this section?
    for field in ("actor", "director", "writer", "producer"):
        try:
            choices = section.listFilterChoices(field=field, libtype=kind)
            say("  listFilterChoices %s/%s: %d values" % (kind, field, len(choices)))
        except Exception as error:
            say("  listFilterChoices %s/%s: REFUSED (%s)" % (kind, field, type(error).__name__))
```

**`probe_c.py` — the URL-length chunk cap (find the refusal shape):**

```python
# ... preamble ...
movie = section_of("movie")
keys = keys_of(movie)
say("movie rating keys:", len(keys))
for chunk in (400, 800, 1200, 1600, len(keys)):
    batch = keys[:chunk]
    path = "/library/metadata/" + ",".join(str(k) for k in batch)
    say("chunk=%4d path-length=%5d ..." % (chunk, len(path)))
    start = time.monotonic()
    try:
        items = movie.fetchItems(batch)
        say("  OK returned=%4d %6.2fs" % (len(items), time.monotonic() - start))
    except Exception as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        say("  REFUSED %s status=%s" % (type(error).__name__, status))
        break
```

**`probe_d.py` — row 180's walk: do `viewCount`/`lastViewedAt` reach the section listing? (+ credit tags in the listing, informational):**

```python
# ... preamble ...
for kind in ("movie", "show"):
    section = section_of(kind)
    data = server.query("/library/sections/%s/all" % section.key)
    rows = [el for el in data if "ratingKey" in el.attrib]
    with_vc = sum(1 for el in rows if "viewCount" in el.attrib)
    zero_vc = sum(1 for el in rows if el.attrib.get("viewCount") == "0")
    with_lv = sum(1 for el in rows if "lastViewedAt" in el.attrib)
    with_ur = sum(1 for el in rows if "userRating" in el.attrib)
    credit_children = sum(1 for el in rows for child in el
                          if child.tag in ("Role", "Director", "Writer", "Producer"))
    say(kind, "items=%d viewCount-present=%d (of which =0: %d) lastViewedAt-present=%d "
        "userRating-present=%d credit-children-in-listing=%d"
        % (len(rows), with_vc, zero_vc, with_lv, with_ur, credit_children))
```

**`probe_e.py` — row 198: does `X-Plex-Container-Size: 0` return `totalSize` on the search URL?**

```python
# ... preamble ...
movie = section_of("movie")
path = "/library/sections/%s/all?type=1&year=1994" % movie.key
data = server.query(path, headers={"X-Plex-Container-Start": "0", "X-Plex-Container-Size": "0"})
say("container-size-0: totalSize=%r size=%r children=%d"
    % (data.attrib.get("totalSize"), data.attrib.get("size"), len(list(data))))
actual = len(movie.fetchItems(path))
say("full fetch of same URL:", actual, "item(s)")
say("VERDICT:", "CONFIRMED" if str(actual) == data.attrib.get("totalSize") else "NOT CONFIRMED")
```

- [ ] **Step 3: Run each probe and capture its output**

```bash
for p in a b c d e; do
  kubectl exec -n media -i deploy/autoposter -- python - < probe_$p.py > probe_$p.out 2>&1
  echo "=== probe_$p exit=$?"
done
```

Expected: each `.out` non-empty, no `UNREACHABLE` line. `probe_c` is expected to either succeed at every chunk (then the cap is ≥ the library size) or print one `REFUSED` line — both are answers.

- [ ] **Step 4: Write the report with the decision table**

Create `docs/research/plex-batch-probe/README.md`: title, date, "read-only throughout", the server identification sentence (reuse 9a's wording, scrubbed), then one section per probe with its full captured output pasted, then the scripts reproduced verbatim (they are the authority for what was sent), then this table with the VERDICT column filled from the output:

```markdown
## Decisions (the gate for plan tasks 4-7)

| # | Question | Verdict | Gates |
| --- | --- | --- | --- |
| D1 | Do Role/Director/Writer/Producer tags ride the batch response (probe b)? | YES / NO | T4, T5. NO ⇒ BLOCKED-FOR-ADJUDICATION (facts C2) |
| D2 | Largest working chunk / refusal shape (probe c) | <number, exception class, status> | T2's `TAG_BATCH_CHUNK` if < 200; T6 |
| D3 | Show-library batch economics (probe a) | <calls, s, ms/item> | recorded; informs nothing structural |
| D4 | `viewCount`/`lastViewedAt`/`userRating` in the listing (probe d) | per attrib: ALL-PRESENT / SPARSE / ABSENT (with the counts) | T7's rows 180/175 branch |
| D5 | `totalSize` at container-size 0 (probe e) | CONFIRMED / NOT CONFIRMED | T7's row-198 commit |
| D6 | Stream `language` attrib spelling (probe a) | <which attribs carry values> | T3's `_stream_languages` field |
| D7 | `listFilterChoices` enumerates actor/director/writer/producer (probe b) | per section/field | T5's resolver (C5: hubSearch only if this is insufficient) |
```

If D1 is NO, add under the table, verbatim: **"STOP: tasks 4 and 5 are BLOCKED-FOR-ADJUDICATION. The controller chooses between a drift-paced reload scan (the 500/week shape), a TMDb-side credits source (row 194's open question resolved toward filmography semantics), or a re-file. Do not dispatch T4/T5 until the facts file records the choice."** Tasks 2, 3 and 6 proceed regardless; T7 proceeds minus its 194/169 bookkeeping.

- [ ] **Step 5: Scrub-check the report, delete the scripts**

```bash
grep -Eo 'https?://[^ )"]+' docs/research/plex-batch-probe/README.md | sort -u
grep -c '<plex-host>\|<plex-url>' docs/research/plex-batch-probe/README.md
rm probe_a.py probe_b.py probe_c.py probe_d.py probe_e.py probe_*.out
```

Expected: the first grep prints nothing (or only `<plex-url>` placeholders — no real scheme+host); the second prints a non-zero count. Paste both results into the report's own "key-grep" line before committing (re-run after pasting; the placeholders count will grow by the pasted line, which is fine).

- [ ] **Step 6: Commit**

```bash
git add docs/research/plex-batch-probe/README.md
git commit --no-gpg-sign -m "docs(probe): phase B batch probes — credits gate, chunk cap, listing walk, totalSize"
```

---

### Task 2: The batched-read machinery

**Files:**
- Modify: `src/autoposter/plex/client.py` (append after `as_int`, `:85-91`, before `_RawMatch`)
- Create: `src/autoposter/collections/enrichment.py`
- Test: `tests/test_plex_tag_batch.py`, `tests/test_collection_enrichment.py`

**Interfaces:**
- Consumes: T1's D2 (chunk cap — if D2's largest working chunk is below 200, set `TAG_BATCH_CHUNK` to it; otherwise 200 stands on probe F's measurement).
- Produces (later tasks rely on these exact names/types):
  - `autoposter.plex.client.TAG_BATCH_CHUNK: int`
  - `autoposter.plex.client.ItemTags` — frozen dataclass, fields `genres, labels, collections, audio_languages, subtitle_languages`, each `tuple[str, ...]`
  - `autoposter.plex.client.fetch_tag_index(section, rating_keys: Sequence[str], chunk_size: int = TAG_BATCH_CHUNK) -> dict[str, ItemTags]` — **blocking**; callers on the event loop wrap it in `asyncio.to_thread`
  - `autoposter.plex.client._iter_metadata_batches(section, rating_keys, chunk_size)` — the chunked fetch T4's credit extractor reuses
  - `autoposter.collections.enrichment.ensure_tags(section, run_cache: dict, rating_keys: Sequence[str], chunk_size: int = TAG_BATCH_CHUNK) -> dict[str, ItemTags]` — async; returns the run's whole tag cache (a superset of the asked-for keys)
  - `autoposter.collections.enrichment.EnrichmentUnavailable(Exception)`

Why module-level functions in `plex/client.py` rather than a `PlexClient` method: the collections engine holds a `section`, not a `PlexClient` (`engine.run_library` is handed the section by `service.reconcile_libraries:345`), and the machinery must be reachable from both the engine (T3) and the scan (T4). The client module is still the home — it is where the plain-data-out discipline lives (`SectionItem`, `_RawMatch`) and these functions keep that discipline exactly: nothing a caller receives is a plexapi object.

- [ ] **Step 1: Write the failing tests**

`tests/test_plex_tag_batch.py`:

```python
"""fetch_tag_index: the chunked multi-key metadata read, plain data out.

The call-count pin is the point (probe F's own recommendation,
.superpowers/sdd/archive/p9a-task-2-report.md ~:535): a regression to
per-item fetching must be a red test, not a slow night.
"""
import math

import pytest

from autoposter.plex.client import ItemTags, fetch_tag_index


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeStream:
    def __init__(self, stream_type, language):
        self.streamType = stream_type
        self.language = language


class FakePart:
    def __init__(self, streams):
        self.streams = streams


class FakeMedia:
    def __init__(self, parts):
        self.parts = parts


class FakeItem:
    def __init__(self, rating_key, genres=(), labels=(), collections=(), streams=()):
        self.ratingKey = rating_key
        self.genres = [FakeTag(g) for g in genres]
        self.labels = [FakeTag(x) for x in labels]
        self.collections = [FakeTag(c) for c in collections]
        self.media = [FakeMedia([FakePart(list(streams))])] if streams else []


class FakeSection:
    """Counts fetchItems calls and records each call's key list."""

    def __init__(self, items):
        self._items = {int(i.ratingKey): i for i in items}
        self.calls: list[list[int]] = []

    def fetchItems(self, ekey):
        assert isinstance(ekey, list) and all(isinstance(k, int) for k in ekey), (
            "the batch seam is plexapi's list-of-ints translation (base.py:334-335); "
            "anything else is a different endpoint"
        )
        self.calls.append(list(ekey))
        return [self._items[k] for k in ekey if k in self._items]


def _items(n):
    return [FakeItem(str(k), genres=("Action", "Crime")) for k in range(1, n + 1)]


def test_call_count_is_ceil_n_over_chunk_exactly():
    section = FakeSection(_items(1955))
    keys = [str(k) for k in range(1, 1956)]
    result = fetch_tag_index(section, keys, chunk_size=200)
    assert len(section.calls) == math.ceil(1955 / 200) == 10
    assert all(len(call) <= 200 for call in section.calls)
    assert len(result) == 1955


def test_result_is_plain_data_keyed_by_string_rating_key():
    item = FakeItem("7", genres=("Horror",), labels=("Overlay",),
                    collections=("Alien Collection",),
                    streams=(FakeStream(2, "English"), FakeStream(3, "Finnish"),
                             FakeStream(1, None)))
    result = fetch_tag_index(FakeSection([item]), ["7"], chunk_size=50)
    tags = result["7"]
    assert isinstance(tags, ItemTags)
    assert tags.genres == ("Horror",)
    assert tags.labels == ("Overlay",)
    assert tags.collections == ("Alien Collection",)
    assert tags.audio_languages == ("English",)
    assert tags.subtitle_languages == ("Finnish",)


def test_item_with_no_tags_is_present_with_empty_tuples_not_absent():
    # "found nothing" is an ANSWER; absence from the dict means "not fetched".
    result = fetch_tag_index(FakeSection([FakeItem("9")]), ["9"], chunk_size=50)
    assert result["9"] == ItemTags((), (), (), (), ())


def test_key_plex_no_longer_answers_is_absent_from_the_result():
    result = fetch_tag_index(FakeSection([FakeItem("1")]), ["1", "2"], chunk_size=50)
    assert "1" in result and "2" not in result


def test_duplicate_tags_deduplicate_preserving_order():
    item = FakeItem("3", streams=(FakeStream(2, "English"), FakeStream(2, "English"),
                                  FakeStream(2, "Finnish")))
    result = fetch_tag_index(FakeSection([item]), ["3"])
    assert result["3"].audio_languages == ("English", "Finnish")


def test_non_numeric_rating_key_raises_rather_than_guessing():
    with pytest.raises(ValueError):
        fetch_tag_index(FakeSection([]), ["not-a-key"])
```

`tests/test_collection_enrichment.py`:

```python
"""ensure_tags: the per-run cache beside run_cache (engine.py:306).

Scoped to the asked-for keys, never the whole library; the failure is
memoised too (BuilderContext.run_cache's own law), and so is a fetched-but-
absent key, so a gone item costs one fetch per pass, not one per definition.
"""
import pytest

from autoposter.collections.enrichment import EnrichmentUnavailable, ensure_tags


class FakeSection:
    def __init__(self, items, fail=False):
        self._items = items
        self.fail = fail
        self.calls = []

    def fetchItems(self, ekey):
        self.calls.append(list(ekey))
        if self.fail:
            raise RuntimeError("boom")
        return [self._items[k] for k in ekey if k in self._items]


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key
        self.genres = []
        self.labels = []
        self.collections = []
        self.media = []


def _section(*keys):
    return FakeSection({int(k): FakeItem(k) for k in keys})


async def test_second_call_fetches_only_the_missing_keys():
    section = _section("1", "2", "3")
    run_cache = {}
    first = await ensure_tags(section, run_cache, ["1", "2"])
    assert set(first) >= {"1", "2"} and len(section.calls) == 1
    second = await ensure_tags(section, run_cache, ["2", "3"])
    assert "3" in second
    assert section.calls[1] == [3], "keys already cached must not be re-fetched"


async def test_fully_cached_ask_makes_no_call_at_all():
    section = _section("1")
    run_cache = {}
    await ensure_tags(section, run_cache, ["1"])
    await ensure_tags(section, run_cache, ["1"])
    assert len(section.calls) == 1


async def test_failure_is_memoised_for_the_pass():
    section = FakeSection({}, fail=True)
    run_cache = {}
    with pytest.raises(EnrichmentUnavailable) as first:
        await ensure_tags(section, run_cache, ["1"])
    assert "RuntimeError" in str(first.value) and "boom" not in str(first.value)
    with pytest.raises(EnrichmentUnavailable):
        await ensure_tags(section, run_cache, ["1"])
    assert len(section.calls) == 1, "a dead server is one fetch per pass, not one per definition"


async def test_gone_key_is_memoised_as_missing_not_refetched():
    section = _section("1")
    run_cache = {}
    result = await ensure_tags(section, run_cache, ["1", "99"])
    assert "99" not in result
    await ensure_tags(section, run_cache, ["99"])
    assert len(section.calls) == 1, "a key Plex does not answer is not asked again this pass"


async def test_empty_ask_makes_no_call():
    section = _section()
    assert await ensure_tags(section, {}, []) == {}
    assert section.calls == []
```

(If the suite's asyncio tests need a marker rather than bare `async def`, match whatever `tests/test_collection_facts_enumeration.py` does — copy its convention exactly.)

- [ ] **Step 2: Run them to see them fail**

```bash
docker compose -p pphb2 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_plex_tag_batch.py tests/test_collection_enrichment.py 2>&1 | tee /app/.superpowers/run-pphb2-red1.log; echo EXIT=\$?"
```

Expected: collection errors — `ImportError: cannot import name 'fetch_tag_index'` and `No module named 'autoposter.collections.enrichment'`.

- [ ] **Step 3: Implement the fetch in `src/autoposter/plex/client.py`**

Append after `as_int` (`:91`), before `_RawMatch`. Set `TAG_BATCH_CHUNK` per D2.

```python
# The batched metadata read (roadmap row 197). Chunk default from phase 9a's
# probe F (10 calls / 16.90s for 1955 movies at chunk=200, ~8ms/item flat
# across chunk sizes -- .superpowers/sdd/archive/p9a-task-2-report.md) and
# bounded by the URL-length cap the phase-B probe measured
# (docs/research/plex-batch-probe/README.md, D2).
TAG_BATCH_CHUNK = 200


@dataclass(frozen=True)
class ItemTags:
    """One item's tag families from the metadata endpoint, as plain data.

    The section listing truncates or strips these (9a's probe verdicts,
    quoted in the FILTER_ATTRIBUTES rows); the batch endpoint returns them
    full. Empty tuples are an ANSWER -- "this item has none" -- which is why
    the fetch stores them: absence from the index means "not fetched", and
    the two must never be conflated (the enrichment refusal law).
    """

    genres: tuple[str, ...]
    labels: tuple[str, ...]
    collections: tuple[str, ...]
    audio_languages: tuple[str, ...]
    subtitle_languages: tuple[str, ...]


def _safe_attr(item: object, name: str) -> object | None:
    """`object.__getattribute__`, so a falsy value can never trigger plexapi's
    partial-object reload -- the same load-bearing read
    `collections/filter_values._listing_value` uses, for the same reason."""
    try:
        return object.__getattribute__(item, name)
    except AttributeError:
        return None


def _uniq(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for v in values if v))


def _tag_names(item: object, attr: str) -> tuple[str, ...]:
    children = _safe_attr(item, attr) or []
    return _uniq(getattr(child, "tag", None) for child in children)


def _stream_languages(item: object, stream_type: int) -> tuple[str, ...]:
    """streamType 2 is audio, 3 is subtitles. The value read is the stream's
    `language` display title (probe decision D6 -- adjust only if that row
    says the production server carries no `language` attrib). `Media`,
    `MediaPart` and streams are plain PlexObjects with no reload guard of
    their own (`filter_values._resolutions` documents the same)."""
    found = []
    for media in _safe_attr(item, "media") or []:
        for part in getattr(media, "parts", None) or []:
            for stream in getattr(part, "streams", None) or []:
                if getattr(stream, "streamType", None) == stream_type:
                    found.append(getattr(stream, "language", None))
    return _uniq(found)


def _iter_metadata_batches(section, rating_keys, chunk_size):
    """Full metadata for the given keys, `ceil(N/chunk)` fetches exactly.

    The seam is plexapi's own list-of-ints translation: `fetchItems` turns a
    list of ints into `/library/metadata/{k1,k2,...}` (base.py:334-335). A
    non-numeric key raises ValueError: rating keys handed here come off
    plexapi items and are always numeric, so a failure to parse is a caller
    bug, not a library state. BLOCKING -- callers on the event loop go
    through `asyncio.to_thread` (`collections/enrichment.py` does).
    """
    keys = [int(key) for key in rating_keys]
    for start in range(0, len(keys), chunk_size):
        yield from section.fetchItems(keys[start:start + chunk_size])


def fetch_tag_index(
    section, rating_keys, chunk_size: int = TAG_BATCH_CHUNK
) -> dict[str, ItemTags]:
    """`{rating_key: ItemTags}` for every key Plex still answers.

    A key in the request but not the result is an item Plex no longer holds
    (or held back): the caller decides what that means -- a `filters:`
    enrichment REFUSES the definition rather than evaluating without it.
    Plain data out: nothing returned is a plexapi object, the same
    discipline as `SectionItem`.
    """
    index: dict[str, ItemTags] = {}
    for item in _iter_metadata_batches(section, rating_keys, chunk_size):
        rating_key = _safe_attr(item, "ratingKey")
        if rating_key is None:
            continue
        index[str(rating_key)] = ItemTags(
            genres=_tag_names(item, "genres"),
            labels=_tag_names(item, "labels"),
            collections=_tag_names(item, "collections"),
            audio_languages=_stream_languages(item, 2),
            subtitle_languages=_stream_languages(item, 3),
        )
    return index
```

- [ ] **Step 4: Implement `src/autoposter/collections/enrichment.py`**

```python
"""The per-run tier-2 enrichment cache (roadmap row 197).

One dict on the pass's `run_cache` (beside `engine.run_library`'s other
scratch, engine.py:306), holding every `ItemTags` fetched so far this pass.
Scoped to the RESOLVED ITEM SETS callers actually ask for -- never the whole
library by default: a 40-item definition costs one request, and the
collectionless builder, whose resolved set IS the library, pays the measured
ceil(N/chunk) and no more. Three memos, per BuilderContext.run_cache's own
law that a failure must be memoised too:

- fetched tags (empty tag families included -- "found none" is an answer),
- keys Plex did not answer (so a gone item costs one fetch per pass),
- the pass-level failure (so a dead server costs one fetch per pass).
"""
import asyncio

from autoposter.plex.client import TAG_BATCH_CHUNK, ItemTags, fetch_tag_index

__all__ = ["EnrichmentUnavailable", "ensure_tags"]

_TAGS_KEY = "plex_enrichment:tags"
_MISSING_KEY = "plex_enrichment:missing"
_FAILED_KEY = "plex_enrichment:failed"


class EnrichmentUnavailable(Exception):
    """The batched read failed this pass. Class-name-only by construction:
    a plexapi failure's message can quote the tokenised URL."""


async def ensure_tags(
    section, run_cache: dict, rating_keys, chunk_size: int = TAG_BATCH_CHUNK
) -> dict[str, ItemTags]:
    """The run's tag cache, guaranteed to have been ASKED about every key in
    `rating_keys`. A key still absent from the result after this returns is
    one Plex did not answer -- the caller's refusal to make, not this
    module's to hide.
    """
    failed = run_cache.get(_FAILED_KEY)
    if failed is not None:
        raise failed
    tags: dict[str, ItemTags] = run_cache.setdefault(_TAGS_KEY, {})
    missing: set[str] = run_cache.setdefault(_MISSING_KEY, set())
    wanted = [str(key) for key in rating_keys]
    to_fetch = [key for key in wanted if key not in tags and key not in missing]
    if not to_fetch:
        return tags
    try:
        fetched = await asyncio.to_thread(fetch_tag_index, section, to_fetch, chunk_size)
    except Exception as error:
        failure = EnrichmentUnavailable(
            "the batched Plex metadata read failed (%s); every definition "
            "needing tier-2 attributes is refused this pass. If Plex is up, "
            "check the chunk cap the phase-B probe measured (docs/research/"
            "plex-batch-probe/README.md)" % type(error).__name__
        )
        run_cache[_FAILED_KEY] = failure
        raise failure from None
    tags.update(fetched)
    missing.update(key for key in to_fetch if key not in fetched)
    return tags
```

- [ ] **Step 5: Run the new tests to see them pass**

```bash
docker compose -p pphb2 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_plex_tag_batch.py tests/test_collection_enrichment.py 2>&1 | tee /app/.superpowers/run-pphb2-green1.log; echo EXIT=\$?"
```

Expected: all pass, `EXIT=0`.

- [ ] **Step 6: Golden gate + neighbourhood**

```bash
docker compose -p pphb2 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_collection_filter_values.py tests/test_collection_filters.py tests/test_builder_engine.py tests/test_collection_config.py 2>&1 | tee /app/.superpowers/run-pphb2-neigh.log; echo EXIT=\$?"
git diff --stat tests/fixtures/collections/golden_port.json
```

Expected: all pass; the diff is empty.

- [ ] **Step 7: Commit**

```bash
git add src/autoposter/plex/client.py src/autoposter/collections/enrichment.py tests/test_plex_tag_batch.py tests/test_collection_enrichment.py
git commit --no-gpg-sign -m "feat(plex): batched multi-key metadata read with per-run enrichment cache"
```

---

### Task 3: The tier-2 accessor branch

**Files:**
- Modify: `src/autoposter/collections/filters.py` (`SOURCE_TIERS` `:170`, the five rows `:545/:624/:638/:647/:753`, the `network` row `:732`, the checksum comment `:520-530`, new helper after `predicates`)
- Modify: `src/autoposter/collections/filter_values.py` (docstring, `__all__`, the view)
- Modify: `src/autoposter/config/schema.py` (`_filters_must_parse_and_be_readable`, `:574-615`)
- Modify: `src/autoposter/collections/engine.py` (`_run_one` `:539-548`, `_passing` `:623-686`)
- Test: `tests/test_collection_filters.py`, `tests/test_collection_filter_values.py`, `tests/test_collection_config.py`, `tests/test_builder_engine.py`

**Interfaces:**
- Consumes: T2's `ItemTags`, `ensure_tags`, `EnrichmentUnavailable`; T1's D6 (already folded into T2's `_stream_languages`).
- Produces (later tasks rely on these exact names):
  - `filters.SOURCE_TIERS` containing `"tier2-batched"`; rows `genre`, `label`, `collection`, `audio_language`, `subtitle_language` on that tier; `network` alone on `"tier2-deferred"`
  - `filters.batched_attributes(group: FilterGroup) -> tuple[str, ...]`
  - `filter_values.BATCHED_ATTRIBUTES: tuple[str, ...]`, `filter_values.EnrichmentNotLoaded(LookupError)`, `PlexItemView(item, tags: ItemTags | None = None)`
  - `engine._passing(definition, items, library, tags: dict[str, ItemTags] | None = None)`

**The C3 law, restated as the acceptance bar:** a tier-2 read happens ONLY through the enrichment pass; the engine computes the need from the filter's table row (no new config); an item absent from the enrichment REFUSES the definition — the refusal is contained exactly like a filter-evaluation failure (`outcome.failed`, empty items, "make no changes"), never an empty membership. **`network` ships nothing false:** its row records the verdict — a TVDb/TMDb source under a DISTINCT name is a separate roadmap row, or the attribute has no meaning on this server — and keeps refusing at config load.

- [ ] **Step 1: Write the failing tests**

In `tests/test_collection_filters.py`, update the tier assertions (the file's `:124-151` block) — the new totals are the transcription's checksum:

```python
# sources: 9 listing / 5 tier2-batched / 1 tier2-deferred / 3 unprobed / 3 search-only
assert by_source["listing"] == [
    "year", "resolution", "audience_rating", "critic_rating", "content_rating",
    "added", "release", "duration", "studio",
]
assert by_source["tier2-batched"] == [
    "genre", "audio_language", "subtitle_language", "label", "collection",
]
assert by_source["tier2-deferred"] == ["network"]
```

Add to the same file:

```python
def test_batched_attributes_reports_only_tier2_batched_predicates():
    from autoposter.collections.filters import batched_attributes, parse_filters
    parsed = parse_filters({"genre": "Horror", "year.gte": 1990, "label.not": "Overlay"})
    assert batched_attributes(parsed) == ("genre", "label")
    assert batched_attributes(parse_filters({"year": 1990})) == ()
```

In `tests/test_collection_filter_values.py`:

```python
def test_batched_attributes_match_the_tier2_batched_rows():
    from autoposter.collections.filter_values import BATCHED_ATTRIBUTES
    from autoposter.collections.filters import FILTER_ATTRIBUTES
    assert BATCHED_ATTRIBUTES == tuple(
        row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-batched"
    )


def test_batched_accessor_reads_the_enrichment_never_the_item():
    from autoposter.collections.filter_values import PlexItemView
    from autoposter.plex.client import ItemTags

    class Explodes:
        def __getattr__(self, name):  # any read of the ITEM here is the N+1 trap
            raise AssertionError("a batched accessor must not touch the item")

    tags = ItemTags(("Horror", "Thriller"), ("Overlay",), (), ("English",), ())
    view = PlexItemView(Explodes(), tags=tags)
    assert view.get("genre") == ("Horror", "Thriller")
    assert view.get("label") == ("Overlay",)
    assert view.get("collection") is None          # empty family -> missing value
    assert view.get("audio_language") == ("English",)
    assert view.get("subtitle_language") is None


def test_batched_attribute_without_tags_raises_enrichment_not_loaded():
    import pytest
    from autoposter.collections.filter_values import EnrichmentNotLoaded, PlexItemView
    with pytest.raises(EnrichmentNotLoaded):
        PlexItemView(object()).get("genre")


def test_tier_one_reads_still_work_with_tags_present():
    from autoposter.collections.filter_values import PlexItemView
    from autoposter.plex.client import ItemTags

    class Item:
        year = 1994

    view = PlexItemView(Item(), tags=ItemTags((), (), (), (), ()))
    assert view.get("year") == 1994
```

In `tests/test_collection_config.py`, flip the `genre:` refusal test at `:514-522` into acceptance and pin the `network` copy:

```python
def test_a_genre_filter_now_loads_because_the_batched_read_feeds_it():
    definition = CollectionDefinition(
        title="Horror", builder="plex_all", params={},
        filters={"genre": "Horror"},
    )
    assert definition.filters == {"genre": "Horror"}


def test_network_still_refuses_and_names_the_stranding():
    with pytest.raises(ValidationError) as error:
        CollectionDefinition(
            title="E4 shows", builder="plex_all", params={},
            filters={"network": "E4"},
        )
    message = str(error.value)
    assert "network" in message
    assert "emits it nowhere" in message
```

(Match the module's existing construction style for `CollectionDefinition` — copy the neighbouring tests' imports and required fields exactly rather than the sketch above.)

In `tests/test_builder_engine.py` (append; reuse the module's existing fakes/harness for `run_library` — the sketch names the behaviours, the module's own idioms carry them):

```python
async def test_a_tier2_filter_enriches_only_the_resolved_set(...):
    # A definition resolving 3 items with filters {"genre": "Horror"} makes
    # exactly ONE fetchItems call listing exactly those 3 keys; items whose
    # ItemTags.genres contains "Horror" survive, others are filtered.

async def test_two_tier2_definitions_share_the_run_cache(...):
    # Two definitions over overlapping sets: the second fetch asks only for
    # keys the first did not.

async def test_an_item_missing_from_the_enrichment_refuses_the_definition(...):
    # FakeSection answers the batch WITHOUT one resolved key: the definition
    # reports failed, applies nothing, and the action string names the
    # enrichment; other definitions in the pass are untouched.

async def test_a_tier1_only_filter_makes_no_batch_call(...):
    # filters {"year.gte": 1990} -> zero fetchItems calls.
```

- [ ] **Step 2: Run to see them fail**

```bash
docker compose -p pphb3 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_collection_filters.py tests/test_collection_filter_values.py tests/test_collection_config.py tests/test_builder_engine.py 2>&1 | tee /app/.superpowers/run-pphb3-red1.log; echo EXIT=\$?"
```

Expected: the new tests fail (`tier2-batched` unknown, `EnrichmentNotLoaded` missing, `genre:` still refused); the untouched neighbours still pass.

- [ ] **Step 3: Move the table rows** (`src/autoposter/collections/filters.py`)

1. `SOURCE_TIERS = ("listing", "probe", "tier2-batched", "tier2-deferred", "unprobed", "search-only")` — and extend the comment block above it (`:154-169`) with one entry:

```python
# - ``tier2-batched`` (phase B): the listing strands it but the batched
#   ``/library/metadata/{k1,k2,...}`` read carries it fully (9a probe F,
#   re-verified for the show library by the phase-B probe). Readable through
#   the engine's enrichment pass ONLY -- a definition whose filters name such
#   a row triggers one batched fetch for its resolved set, and an item the
#   batch did not answer for REFUSES the definition rather than evaluating
#   with a silently-missing value.
```

2. Change `"tier2-deferred"` → `"tier2-batched"` on the `genre` (`:545`), `audio_language` (`:624`), `subtitle_language` (`:638`), `label` (`:647`) and `collection` (`:753`) rows, appending to each row's note (keep the probe history; add the phase-B sentence), e.g. for `genre`:

```text
" PHASE B: moved to `tier2-batched` -- the batched metadata read returns the
family full and untruncated (9a probe F: 3 sampled items matched the
single-key endpoint exactly), so `filters:` may name it; the engine pays one
batched fetch for the definition's resolved set."
```

and equivalently for the other four (each citing its own probe F evidence: labels present in batch, collections present, streams present with 4/7 per item).

3. `network` (`:732`) stays `"tier2-deferred"`; append the verdict to its note:

```text
" PHASE B VERDICT, recorded rather than shipped false: the batch read cannot
conjure an attrib Plex 1.43.4 emits nowhere (0/284 in the listing AND absent
from /library/metadata), so `network` stays refused. Sourcing it from
TVDb/TMDb under a DISTINCT name is a separate roadmap row if anyone wants
it; conflating it with `studio` stays refused by name."
```

4. Update the checksum comment (`:520-530`): sources become `9 listing / 5 tier2-batched / 1 tier2-deferred / 3 unprobed / 3 search-only`. Types, kinds and search_kinds totals are unchanged in this task.

5. Add the helper after `predicates` (the existing traversal function near `BY_NAME`):

```python
def batched_attributes(group: FilterGroup) -> tuple[str, ...]:
    """The distinct `tier2-batched` attribute names this parsed filter reads,
    in first-appearance order. What the engine uses to decide whether a
    definition needs the enrichment pass at all -- computed from the table
    row, never from config (there is no knob to declare it)."""
    seen: dict[str, None] = {}
    for predicate in predicates(group):
        if predicate.attribute.source == "tier2-batched":
            seen.setdefault(predicate.attribute.name, None)
    return tuple(seen)
```

Export `batched_attributes` in `__all__` (`:118-142`).

- [ ] **Step 4: The view** (`src/autoposter/collections/filter_values.py`)

1. `__all__` grows `"BATCHED_ATTRIBUTES"` and `"EnrichmentNotLoaded"`.
2. After `DEFERRED_ATTRIBUTES` (`:176-178`):

```python
BATCHED_ATTRIBUTES: tuple[str, ...] = tuple(
    row.name for row in FILTER_ATTRIBUTES if row.source == "tier2-batched"
)

# Table attribute name -> the ItemTags field the enrichment carries it as.
_BATCHED_FIELDS: dict[str, str] = {
    "genre": "genres",
    "label": "labels",
    "collection": "collections",
    "audio_language": "audio_languages",
    "subtitle_language": "subtitle_languages",
}


class EnrichmentNotLoaded(LookupError):
    """A tier-2 attribute was read on a view built without its enrichment.

    Unreachable when the engine is doing its job -- `_run_one` refuses the
    definition before evaluation if any resolved item lacks enrichment -- so
    reaching this means a caller built the view by hand. Raised, never
    answered with None: None means "this item has no value", which the table
    turns into a defined match result, and that is exactly the wrong-but-
    plausible answer the tier system exists to prevent.
    """
```

3. `PlexItemView` becomes:

```python
class PlexItemView:
    """``ItemView`` over one resolved plexapi item, plus (optionally) the
    item's batched enrichment for the tier-2 rows."""

    def __init__(self, item: object, tags=None) -> None:
        self._item = item
        self._tags = tags  # plex.client.ItemTags | None

    def get(self, attribute: str, /) -> object | None:
        field = _BATCHED_FIELDS.get(attribute)
        if field is not None:
            if self._tags is None:
                raise EnrichmentNotLoaded(
                    f"{attribute!r} is a tier-2 attribute and this view was "
                    "built without its enrichment -- the engine fetches one "
                    "batched read per resolved set; a direct caller passes "
                    "`tags=` (see collections/enrichment.ensure_tags)"
                )
            values = getattr(self._tags, field)
            return tuple(values) or None
        accessor = _ACCESSORS.get(attribute)
        ... # the existing body, unchanged from here down
```

4. Keep the existing `AttributeNotInListing` branch text but fix the `tier2-deferred` copy (it now describes only `network`): replace the `elif row.source == "tier2-deferred":` message with

```python
why = (
    "Plex emits it nowhere on this server (0/284 shows in the listing AND "
    "absent from /library/metadata -- 9a's probe), so no read at any tier "
    "can answer it; a TVDb/TMDb-sourced equivalent would be a different "
    "attribute under a distinct name"
)
```

5. Update the module docstring: the probe paragraph's "One shipped and six deferred" story gains a phase-B paragraph — five of the six moved to the batched tier fed by `/library/metadata/{k1,k2,…}`; `network` alone stays stranded. Keep the history; do not rewrite it.

- [ ] **Step 5: Config load** (`src/autoposter/config/schema.py:586-615`)

In `_filters_must_parse_and_be_readable`, admit the batched tier and specialise the deferred copy:

```python
for predicate in predicates(parsed):
    row = predicate.attribute
    if row.name in SHIPPED_ATTRIBUTES or row.source == "tier2-batched":
        continue
    if row.source == "tier2-deferred":
        why = (
            "Plex 1.43.4 emits it nowhere -- 9a's probe found 0/284 shows "
            "carry it in the listing AND it is absent from /library/metadata, "
            "so no batched read can answer it either (the batched tier that "
            "phase B opened feeds genre/label/collection/audio_language/"
            "subtitle_language, not this). A TVDb/TMDb-sourced equivalent "
            "would be a different attribute under a distinct name"
        )
    else:
        why = ( ... the existing `unprobed` copy, unchanged ... )
    raise ValueError(
        f"{self.title!r} cannot filter on {row.name!r} at {predicate.field}: "
        f"that attribute's source tier is {row.source!r}. {why}. "
        "Filterable today: "
        + ", ".join(SHIPPED_ATTRIBUTES + BATCHED_ATTRIBUTES)
    )
```

Import `BATCHED_ATTRIBUTES` beside `SHIPPED_ATTRIBUTES` (`:576`). Update the validator's docstring (`:545-572`): the source-tier half now has THREE outcomes (listing ships free, batched ships through the enrichment pass, deferred/unprobed refuse) — say so in two sentences, keep the rest.

- [ ] **Step 6: The engine wiring** (`src/autoposter/collections/engine.py`)

1. Imports: `from autoposter.collections.enrichment import ensure_tags` and `from autoposter.collections.filters import batched_attributes, parse_filters` (extend the existing filters import).

2. In `_run_one`, replace the `:539-553` block:

```python
    items = resolved.items
    filter_failed = False
    filter_emptied_a_non_empty_set = False
    if definition.filters is not None:
        had_items = bool(items)
        tags = None
        needed: tuple[str, ...] = ()
        try:
            needed = batched_attributes(parse_filters(definition.filters))
        except Exception:
            # A filter that does not parse is _passing's own report; the
            # enrichment pre-step stays out of its way.
            needed = ()
        if needed:
            keys = [str(getattr(item, "ratingKey", "")) for item in items]
            try:
                fetched = await ensure_tags(section, ctx.run_cache, keys)
            except Exception:
                logger.exception(
                    "%s: could not enrich %r for its tier-2 filter "
                    "attributes (%s); nothing was applied to it this pass",
                    library, definition.title, ", ".join(needed),
                )
                fetched = None
            if fetched is None or any(key not in fetched for key in keys):
                # The refusal law (facts C3): an item the batch did not
                # answer for must never evaluate as "has no tags" -- that
                # is a full, plausible, wrong membership. Same containment
                # as a filter that could not run.
                outcome.failed = True
                filter_failed = True
                items = []
                outcome.actions.append(
                    "%r: could not read %s for every resolved item (the "
                    "batched Plex metadata read failed or skipped items), "
                    "so the filter was not evaluated and nothing was "
                    "changed this pass"
                    % (definition.title, ", ".join(needed))
                )
            else:
                tags = fetched
        if not filter_failed:
            kept = _passing(definition, items, library, tags=tags)
            if kept is None:
                outcome.failed = True
                filter_failed = True
                items = []
            else:
                outcome.filtered = len(items) - len(kept)
                items = kept
        filter_emptied_a_non_empty_set = had_items and not items
```

(`ctx` is `_run_one`'s existing `BuilderContext` parameter, built at `:339-349`; its `run_cache` is the pass's shared dict.) Reading `item.ratingKey` off a resolved item is reload-safe: `ratingKey` is never `None`/`[]` on a real item, and `resolve.build_owned_index:68` already reads it in this same pass.

3. `_passing` gains the parameter and threads it through:

```python
def _passing(definition, items, library, tags=None) -> list | None:
    ...
        return [
            item for item in items
            if evaluate(parsed, PlexItemView(item, tags=None if tags is None
                                             else tags.get(str(getattr(item, "ratingKey", "")))),
                        now=now)
        ]
```

Update `_passing`'s docstring: "nothing here reaches Plex" still holds — the enrichment was fetched by `_run_one` BEFORE this stage; this stage only reads the dict. Say so in one sentence where the old text said the view reads only listing values.

- [ ] **Step 7: Run all the task's tests to green, then the golden gate**

```bash
docker compose -p pphb3 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_collection_filters.py tests/test_collection_filter_values.py tests/test_collection_config.py tests/test_builder_engine.py tests/test_collection_filter_oracle.py tests/test_smart_filter_engine.py 2>&1 | tee /app/.superpowers/run-pphb3-green1.log; echo EXIT=\$?"
git diff --stat tests/fixtures/collections/golden_port.json
```

Expected: all pass; empty diff. (The filter oracle exercises tier-1 evaluation and must be untouched by an enrichment that only activates on tier-2 rows.)

- [ ] **Step 8: Commit**

```bash
git add src/autoposter/collections/filters.py src/autoposter/collections/filter_values.py src/autoposter/config/schema.py src/autoposter/collections/engine.py tests/test_collection_filters.py tests/test_collection_filter_values.py tests/test_collection_config.py tests/test_builder_engine.py
git commit --no-gpg-sign -m "feat(filters): tier-2 batched accessors -- genre/label/collection/languages filterable, network's verdict recorded"
```

---

### Task 4: The credits table and the scan

> **CONFIRMED-BY-T1: D1.** Written on the D1=YES branch (credit tags ride the batch; person key = the Plex tag name — upstream's own semantic, the library's credit data). If D1 is NO this task is BLOCKED-FOR-ADJUDICATION (Global Constraint 11) and none of the code below is written until the facts file records the controller's choice. The controller also re-checks D3 (show-library economics) before dispatch.

**Files:**
- Modify: `src/autoposter/db/models.py` (after `ItemFacts`, `:246`; `MediaItem` gains one column beside `facts_attempted_at`, `:50`)
- Create: `alembic/versions/<rev>_item_credits.py`
- Modify: `src/autoposter/plex/client.py` (append after `fetch_tag_index`)
- Create: `src/autoposter/collections/credits.py`
- Modify: `src/autoposter/config/schema.py` (`SchedulerConfig`, `:1033-1056`), `src/autoposter/scheduler/jobs.py` (after `make_drift_job`, `:198-217`), `src/autoposter/app.py` (`:294`)
- Test: `tests/test_collection_credits.py`, plus the scheduler-job test beside the drift job's existing one (find it: `grep -rl make_drift_job tests/`)

**Interfaces:**
- Consumes: T2's `_iter_metadata_batches`, `_safe_attr`, `_tag_names`, `_uniq`, `TAG_BATCH_CHUNK`.
- Produces (T5 relies on these exact names/types):
  - `db.models.ItemCredit` — composite PK `(item_id, kind, person)`; `kind` in `("actor", "director", "writer", "producer")`; `person` is the Plex tag name
  - `db.models.MediaItem.credits_attempted_at: datetime | None`
  - `plex.client.CreditTags` — frozen dataclass, fields `actors, directors, writers, producers`, each `tuple[str, ...]`
  - `plex.client.fetch_credit_index(section, rating_keys, chunk_size=TAG_BATCH_CHUNK) -> dict[str, CreditTags]`
  - `collections.credits.CREDIT_KINDS: tuple[str, ...]`
  - `collections.credits.enumerate_credits(session, kind, *, library, library_type) -> list[tuple[str, int]]` — `(person, item_count)`, most-appearances first, ties on person ascending
  - `collections.credits.credits_coverage(session, *, library, library_type) -> tuple[int, int]` — `(attempted, total)`
  - `collections.credits.scan_credits(session, server, config) -> str`

**The C4 shape, restated as the acceptance bar:** a DEDICATED composite-PK mapping table in the `ImdbEpisode`/`ImdbRating` mold — never a widening of `ItemFacts` (scalar-per-item with `UNIQUE(item_id)` is the wrong shape for many-to-many, as Phase A's C3 predicted). Rows exist only for items the library contains (the `ImdbRating` size discipline — FK CASCADE off `media_items` enforces it). The attempt stamp lives on the ITEM: found-no-credits is a stamped item with zero rows; absent-means-unvisited. Refusals never cached: an item the batch did not answer for gets no stamp and no rows. The missing-value rule verbatim from `facts_enumeration.py:18-24`: no row means ABSENT from every query — never a bucket, never a zero.

- [ ] **Step 1: Write the failing tests**

`tests/test_collection_credits.py` (copy the DB session fixture usage from `tests/test_collection_facts_enumeration.py` — same fixtures, same async convention):

```python
"""The credits cache: scan, attempt stamp, missing-value rule, counted enumeration."""
import pytest
from sqlalchemy import select

from autoposter.collections.credits import (
    CREDIT_KINDS, credits_coverage, enumerate_credits, scan_credits,
)
from autoposter.db.models import ItemCredit, MediaItem


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeItem:
    def __init__(self, rating_key, actors=(), directors=(), writers=(), producers=()):
        self.ratingKey = rating_key
        self.roles = [FakeTag(a) for a in actors]
        self.directors = [FakeTag(d) for d in directors]
        self.writers = [FakeTag(w) for w in writers]
        self.producers = [FakeTag(p) for p in producers]


class FakeSection:
    def __init__(self, key, type_, items):
        self.key = key
        self.type = type_
        self._items = {int(i.ratingKey): i for i in items}
        self.calls = []

    def fetchItems(self, ekey):
        self.calls.append(list(ekey))
        return [self._items[k] for k in ekey if k in self._items]


class FakeLibrary:
    def __init__(self, sections):
        self._sections = {s_name: s for s_name, s in sections.items()}

    def section(self, name):
        return self._sections[name]


class FakeServer:
    def __init__(self, sections):
        self.library = FakeLibrary(sections)


# seed helper: insert MediaItem rows (kind/library/rating_key/title) the way
# tests/test_collection_facts_enumeration.py seeds its items -- copy that
# helper's shape.


async def test_scan_writes_rows_and_stamps_the_attempt(session, config):
    # two movies: one with credits, one with none. After scan_credits:
    # - both items carry credits_attempted_at (found-no-credits IS an answer)
    # - ItemCredit rows exist only for the first, one per (kind, person)
    ...


async def test_an_unanswered_key_is_neither_stamped_nor_written(session, config):
    # FakeSection omits one seeded item from every batch answer: that item
    # keeps credits_attempted_at IS NULL and zero rows -- a refusal never
    # enters the cache.
    ...


async def test_rescan_replaces_an_items_rows(session, config):
    # First scan: actor "A" and "B". Second scan: the fake now returns only
    # "A". The item's rows afterwards are exactly {"A"} -- stale credits do
    # not accumulate.
    ...


async def test_enumerate_credits_counts_most_first_ties_on_name(session, config):
    # Three movies: X acted-in by ("Ann", "Bob"), ("Ann",), ("Cy",).
    # enumerate_credits(..., "actor") == [("Ann", 2), ("Bob", 1), ("Cy", 1)]
    # and enumerate_credits(..., "director") does not see actors.
    ...


async def test_no_row_means_absent_never_a_bucket(session, config):
    # An item with a stamped attempt and zero rows appears in NO kind's
    # enumeration and inflates no count.
    ...


async def test_coverage_counts_attempts_against_library_size(session, config):
    # 3 items, 2 stamped -> (2, 3). The unanswered item from the scan test
    # is what keeps this number honest.
    ...


async def test_scan_call_count_is_ceil_n_over_chunk(session, config):
    # 5 seeded movies, chunk_size threaded as 2 -> 3 fetchItems calls.
    ...
```

Flesh each `...` with the seed helper + real assertions — the docstring lines above each are the behaviour contract; no test may pass vacuously. Add one scheduler test beside the drift job's existing test (same file, same fixtures): `make_credits_job` reads `scheduler.credits_scan_days` live off the holder and its `run` calls `scan_credits` with a server built from `server_factory` in a thread.

- [ ] **Step 2: Run to see them fail**

```bash
docker compose -p pphb4 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_collection_credits.py 2>&1 | tee /app/.superpowers/run-pphb4-red1.log; echo EXIT=\$?"
```

Expected: `No module named 'autoposter.collections.credits'`.

- [ ] **Step 3: The model** (`src/autoposter/db/models.py`)

After `ItemFacts` (`:246`):

```python
class ItemCredit(Base):
    """One (item, credit kind, person) fact from Plex's own credit tags.

    The dedicated many-to-many table roadmap row 197 exists to decide
    (adjudication C4): credits cannot live on ``item_facts``, whose
    UNIQUE(item_id) scalar shape is wrong for one-item-many-people. Composite
    PK in the ``ImdbEpisode`` mold; rows exist only for items the library
    contains (FK CASCADE -- the ``ImdbRating`` size discipline). ``person``
    is the PLEX TAG NAME -- the library's own credit data, upstream's
    semantic for its person packs -- not a TMDb id; a TMDb-fed person
    collection is a DIFFERENT membership under the same name and says so
    where it ships.

    The missing-value rule, verbatim from ``collections/facts_enumeration``:
    no row means ABSENT from every query -- never a bucket, never a zero.
    "We looked and found none" is ``media_items.credits_attempted_at`` set
    with zero rows here; "unvisited" is the stamp being NULL.
    """

    __tablename__ = "item_credits"
    __table_args__ = (
        Index("ix_item_credits_kind_person", "kind", "person"),
    )

    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("media_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # actor | director | writer | producer -- collections/credits.CREDIT_KINDS
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    person: Mapped[str] = mapped_column(String(255), primary_key=True)
```

Add `Index` to the existing `sqlalchemy` import line. On `MediaItem`, directly under `facts_attempted_at` (`:50`):

```python
    # The credits scan's "we looked" stamp (roadmap rows 197/194) -- the same
    # law facts_attempted_at carries: stamped on every VISITED item, found-
    # no-credits included; NULL means unvisited, and only unvisited.
    credits_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: The migration**

```bash
docker compose -p pphb4 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test alembic heads
python -c "import uuid; print(uuid.uuid4().hex[:12])"
```

Expected: `alembic heads` prints exactly ONE head (the tree carries a merge revision; if two heads print, STOP and report — do not invent a merge). Create `alembic/versions/<new-id>_item_credits.py` in the `c1a7f30b9e42` mold, `revision` = the generated id, `down_revision` = the printed head:

```python
"""item credits

Revision ID: <new-id>
Revises: <head>
Create Date: <today> 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '<new-id>'
down_revision: Union[str, Sequence[str], None] = '<head>'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'item_credits',
        sa.Column('item_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('person', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['item_id'], ['media_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('item_id', 'kind', 'person'),
    )
    op.create_index('ix_item_credits_kind_person', 'item_credits', ['kind', 'person'])
    # Nullable, no default, no backfill -- NULL means "never scanned", which
    # is true of every existing row (c1a7f30b9e42's reasoning verbatim).
    op.add_column(
        'media_items',
        sa.Column('credits_attempted_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('media_items', 'credits_attempted_at')
    op.drop_index('ix_item_credits_kind_person', table_name='item_credits')
    op.drop_table('item_credits')
```

- [ ] **Step 5: The credit extractor** (`src/autoposter/plex/client.py`, after `fetch_tag_index`)

```python
@dataclass(frozen=True)
class CreditTags:
    """One item's credit families from the metadata endpoint, as plain data.

    Same discipline as ``ItemTags``; separate class because its consumers are
    different (the credits SCAN, not the filter enrichment) and the two must
    stay independently cheap. Empty tuples are "credited nobody", an answer.
    """

    actors: tuple[str, ...]
    directors: tuple[str, ...]
    writers: tuple[str, ...]
    producers: tuple[str, ...]


def fetch_credit_index(
    section, rating_keys, chunk_size: int = TAG_BATCH_CHUNK
) -> dict[str, CreditTags]:
    """`{rating_key: CreditTags}` for every key Plex still answers.

    Rides the same batched read as ``fetch_tag_index`` (`ceil(N/chunk)`
    calls); the phase-B probe (docs/research/plex-batch-probe/README.md, D1)
    is what established the batch response carries Role/Director/Writer/
    Producer at all. BLOCKING, like its sibling.
    """
    index: dict[str, CreditTags] = {}
    for item in _iter_metadata_batches(section, rating_keys, chunk_size):
        rating_key = _safe_attr(item, "ratingKey")
        if rating_key is None:
            continue
        index[str(rating_key)] = CreditTags(
            actors=_tag_names(item, "roles"),
            directors=_tag_names(item, "directors"),
            writers=_tag_names(item, "writers"),
            producers=_tag_names(item, "producers"),
        )
    return index
```

- [ ] **Step 6: The scan and the enumeration** (`src/autoposter/collections/credits.py`)

```python
"""The library-wide credit scan and its counted enumeration (rows 197/194).

The scan is whole-library per run, NOT drift-paced, and that is an argument
rather than an oversight: Plex is local and unmetered, the currency is round
trips (row 197's explicit non-inheritance from Phase A's rate budget), and
the batched read makes a full movie-library scan ceil(1955/200) = 10
requests (~17s, measured). What IS inherited from Phase A in shape: the
attempt stamped whether or not it found anything, and a refusal that never
enters the cache.

The enumeration is `facts_enumeration.enumerate_values`' GROUP BY shape --
it returns (value, count), which is the depth/limit semantics row 194 needs
and which no listFilterChoices call can answer (values, never counts).
"""
import asyncio
import logging

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autoposter.db.models import ItemCredit, MediaItem
from autoposter.plex.client import TAG_BATCH_CHUNK, fetch_credit_index

logger = logging.getLogger(__name__)

__all__ = [
    "CREDIT_KINDS", "credits_coverage", "enumerate_credits",
    "scan_credits", "scan_library_credits",
]

CREDIT_KINDS = ("actor", "director", "writer", "producer")

# credit kind -> the CreditTags field carrying it.
_FIELD_FOR_KIND = {
    "actor": "actors",
    "director": "directors",
    "writer": "writers",
    "producer": "producers",
}

# Library-type spelling -> media_items.kind rows, facts_enumeration's own map:
# a Show library's credits are the SHOWS' (episode credits are not scanned).
_KINDS = {"Movie": ("movie",), "Show": ("show",)}

# Plex section.type -> our library-type spelling (collections/service.py's
# LIBRARY_TYPES, restated here to keep this module import-light).
_LIBRARY_TYPES = {"movie": "Movie", "show": "Show"}


async def scan_library_credits(
    session: AsyncSession, section, library: str, library_type: str,
    chunk_size: int = TAG_BATCH_CHUNK,
) -> tuple[int, int]:
    """Scan one section. Returns (items stamped, credit rows written).

    Per answered item: its rows are REPLACED (delete + insert -- a person
    Plex no longer credits does not linger) and the attempt is stamped.
    An item the batch did not answer for is untouched: no stamp, no rows,
    so it stays "unvisited" and honest (refusals never cached).
    """
    rows = (
        await session.execute(
            select(MediaItem.id, MediaItem.rating_key).where(
                MediaItem.library == library,
                MediaItem.kind.in_(_KINDS.get(library_type, ())),
            )
        )
    ).all()
    if not rows:
        return (0, 0)
    by_key = {str(rating_key): item_id for item_id, rating_key in rows}
    fetched = await asyncio.to_thread(
        fetch_credit_index, section, list(by_key), chunk_size
    )
    written = 0
    answered_ids = []
    for rating_key, credits in fetched.items():
        item_id = by_key.get(rating_key)
        if item_id is None:
            continue
        answered_ids.append(item_id)
        await session.execute(
            delete(ItemCredit).where(ItemCredit.item_id == item_id)
        )
        for kind, field in _FIELD_FOR_KIND.items():
            for person in getattr(credits, field):
                session.add(ItemCredit(item_id=item_id, kind=kind, person=person))
                written += 1
    if answered_ids:
        await session.execute(
            update(MediaItem)
            .where(MediaItem.id.in_(answered_ids))
            .values(credits_attempted_at=func.now())
        )
    return (len(answered_ids), written)


async def scan_credits(session: AsyncSession, server, config) -> str:
    """Every configured collections library, one summary string for the job
    record. A library that fails is logged and reported, never fatal to the
    others -- `service.reconcile_libraries`' own containment shape.
    """
    parts = []
    for name in config.collections.libraries:
        try:
            section = await asyncio.to_thread(server.library.section, name)
            library_type = _LIBRARY_TYPES.get(section.type)
            if library_type is None:
                parts.append("%s: skipped (unsupported type)" % name)
                continue
            stamped, written = await scan_library_credits(
                session, section, name, library_type
            )
            await session.commit()
            parts.append("%s: %d item(s) scanned, %d credit(s)" % (name, stamped, written))
        except Exception as error:  # class name only -- messages can quote URLs
            logger.exception("credits scan failed for %r", name)
            parts.append("%s: failed (%s)" % (name, type(error).__name__))
    return "; ".join(parts) or "no collections libraries configured"


async def enumerate_credits(
    session: AsyncSession, kind: str, *, library: str, library_type: str
) -> list[tuple[str, int]]:
    """`(person, item_count)` for one credit kind, most-appearances first,
    ties on the person ascending -- the total order facts_enumeration keeps,
    for the same reason (stable family creation between passes)."""
    stmt = (
        select(ItemCredit.person, func.count())
        .select_from(ItemCredit)
        .join(MediaItem, MediaItem.id == ItemCredit.item_id)
        .where(
            ItemCredit.kind == kind,
            MediaItem.library == library,
            MediaItem.kind.in_(_KINDS.get(library_type, ())),
        )
        .group_by(ItemCredit.person)
        .order_by(func.count().desc(), ItemCredit.person.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [(str(person), int(count)) for person, count in rows if str(person)]


async def credits_coverage(
    session: AsyncSession, *, library: str, library_type: str
) -> tuple[int, int]:
    """(items whose credits have been attempted, items in the library) --
    `facts_enumeration.coverage`'s twin, over the credits stamp."""
    kinds = _KINDS.get(library_type, ())
    base = select(func.count()).select_from(MediaItem).where(
        MediaItem.library == library, MediaItem.kind.in_(kinds)
    )
    total = (await session.execute(base)).scalar_one()
    attempted = (
        await session.execute(base.where(MediaItem.credits_attempted_at.is_not(None)))
    ).scalar_one()
    return int(attempted), int(total)
```

- [ ] **Step 7: Config + job + registration**

`src/autoposter/config/schema.py`, in `SchedulerConfig` beside `drift_days` (`:1052`):

```python
    # The credits scan's cadence (roadmap rows 197/194). Whole-library per
    # run, deliberately unpaced: the batched read is ceil(N/chunk) requests
    # (~10 for the measured movie library), so there is nothing a 500-item
    # batch valve would be protecting. Weekly, like drift: credits change on
    # library edits, not on a clock.
    credits_scan_days: int = 7
```

`src/autoposter/scheduler/jobs.py`, after `make_drift_job`:

```python
def make_credits_job(holder: ConfigHolder, server_factory: Callable[[], object]) -> Job:
    """Build the scheduled library-credits scan (roadmap rows 197/194).

    `server_factory` is the same zero-argument connected-PlexServer contract
    `make_collections_job` takes, run in a thread for the same reason.
    Settings come off the holder per run, so an edit is live.
    """

    async def run(session: AsyncSession) -> str:
        server = await asyncio.to_thread(server_factory)
        return await scan_credits(session, server, holder.current)

    return Job(
        name="credits_scan",
        interval_seconds=lambda: holder.current.scheduler.credits_scan_days * 24 * 3600,
        run=run,
    )
```

Import `scan_credits` from `autoposter.collections.credits` at the top of `jobs.py`. In `src/autoposter/app.py:294`, register beside the drift job, passing the same `server_factory` object `make_collections_job` receives at `:290` (use the local name that call site already uses):

```python
            scheduler_jobs.append(make_credits_job(holder, server_factory))
```

and extend the `scheduler_jobs` import block (`:51-52`) with `make_credits_job`.

- [ ] **Step 8: Migration up, tests to green, golden gate**

```bash
docker compose -p pphb4 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "alembic upgrade head && pytest -q tests/test_collection_credits.py tests/test_collection_facts_enumeration.py 2>&1 | tee /app/.superpowers/run-pphb4-green1.log; echo EXIT=\$?"
docker compose -p pphb4b -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_app.py $(grep -rl make_drift_job tests/ | tr '\n' ' ') 2>&1 | tee /app/.superpowers/run-pphb4-green2.log; echo EXIT=\$?"
git diff --stat tests/fixtures/collections/golden_port.json
```

Expected: all pass (the migration applies cleanly on top of the single head); empty diff.

- [ ] **Step 9: Commit**

```bash
git add src/autoposter/db/models.py alembic/versions/*_item_credits.py src/autoposter/plex/client.py src/autoposter/collections/credits.py src/autoposter/config/schema.py src/autoposter/scheduler/jobs.py src/autoposter/app.py tests/test_collection_credits.py <the scheduler test file touched>
git commit --no-gpg-sign -m "feat(credits): dedicated item_credits cache with batched library scan"
```

(Stage the migration by its literal generated filename, not the glob, when committing.)

---

### Task 5: The people search rows and the counted person families

> **CONFIRMED-BY-T1: D1, D7.** Written on the branch where credit tags ride the batch (D1=YES — T4 shipped) AND `listFilterChoices` enumerates `actor`/`director`/`writer`/`producer` on the sections that need them (D7). If D1 is NO this task is BLOCKED-FOR-ADJUDICATION with T4. If D7 shows `listFilterChoices` refusing a needed field, this task is BLOCKED-FOR-ADJUDICATION on its own: facts C5 forbids building the hubSearch fallback speculatively — the controller decides whether to build it (Kometa's `modules/plex.py:1273-1280` shape) or re-file. A partial chain re-files the packs with its reason (the 10b discipline): the packs flip READY **only if the whole chain — scan → counts → query — ships**.

**Files:**
- Modify: `src/autoposter/collections/filters.py` (four rows appended after `country`, `:863-887`; checksum comment)
- Create: `src/autoposter/collections/builders/credits_family.py`
- Modify: `src/autoposter/collections/builders/__init__.py` (import + `register(CreditsFamilyBuilder())`)
- Modify: `src/autoposter/collections/catalog.py` (`_PERSON_PACKS` block, `:1838-1901`)
- Test: `tests/test_collection_filters.py`, `tests/test_builder_credits_family.py`, `tests/test_collection_catalog.py`

**Interfaces:**
- Consumes: T4's `enumerate_credits(session, kind, *, library, library_type) -> list[tuple[str, int]]`, `credits_coverage`, `CREDIT_KINDS`; the existing `LibraryTagResolver` (`builders/plex_search.py:375`), `parse_filters(…, searching=True, base="any")`, `build_search_url(group, *, libtype, sort_by=(), limit=None, resolve_tag)` (`search_url.py:106`), `reconcile_smart_collection`, `derive_keys`, `family_titles`, `require_library_type`, `SmartContext`.
- Produces: `builder: credits_family` with params `{type, depth, limit, …}`; the four `FILTER_ATTRIBUTES` rows `actor`/`director`/`writer`/`producer` (BY_NAME entries — the queries become WRITABLE, row 194's "blocker one" closed).

**The open question row 194 demands be answered explicitly, answered:** upstream's per-person collection is a search on the Plex ACTOR TAG — the library's own credit data — and that is what ships: the scan reads Plex tags (T4), the counts count them, and each collection's membership is a Plex tag search. A `tmdb_actor`-filmography family would be a DIFFERENT membership under the same name; the pack descriptions disclose the choice (Step 6).

- [ ] **Step 1: The four table rows** (failing tests first)

In `tests/test_collection_filters.py`, update the checksums: **25 rows** — types `13 tag / 1 str / 3 int / 2 float / 3 date / 1 duration / 2 bool`; sources `9 listing / 5 tier2-batched / 1 tier2-deferred / 7 unprobed / 3 search-only`; `kinds` Counter `{("movie","show"): 14, ("movie",): 10, ("show",): 1}`; `search_kinds` Counter `{("movie","show"): 17, ("movie",): 7, ("show",): 1}`. Add:

```python
def test_the_people_rows_are_searchable_and_libtype_gated():
    for name in ("actor", "director", "writer", "producer"):
        row = BY_NAME[name]
        assert row.type == "tag" and row.source == "unprobed" and row.filterable
    assert BY_NAME["actor"].search_kinds == ("movie", "show")
    assert BY_NAME["actor"].field_for("show") == "actor"
    for name in ("director", "writer", "producer"):
        assert BY_NAME[name].search_kinds == ("movie",)
        with pytest.raises(ValueError):
            BY_NAME[name].field_for("show")
```

Run (`pphb5` project) to see the checksum and new tests fail; then append to `FILTER_ATTRIBUTES` after the `country` row, under a `# --- rows phase B added ---` comment:

```python
    FilterAttribute(
        "actor", "tag", _BOTH, "unprobed",
        "Plex's `<Role>` child element -- the library's own credit data, the "
        "same tags the phase-B credits cache scans. Row 169's first entry: "
        "the row's existence is what makes a per-person query WRITABLE at "
        "all (roadmap row 194 blocker one). `unprobed` client-side for the "
        "reason `plays` carries that tier: 9a never asked whether `<Role>` "
        "reaches the section listing completely, so there is no verdict to "
        "cite -- the credits CACHE, not a listing accessor, is where a "
        "client-side read would come from, and that is a facts-tier "
        "decision row 156 owns. Values resolve through `listFilterChoices` "
        "(the phase-B probe measured the field answering on both sections); "
        "Kometa's hubSearch fallback (plex.py:1273-1280) is deliberately "
        "NOT built until a real miss shows the choices listing insufficient.",
        search_field="actor", show_search_field=None,
        search_kinds=_BOTH, filterable=True,
    ),
    FilterAttribute(
        "director", "tag", ("movie",), "unprobed",
        "Plex's `<Director>` child element. MOVIE-ONLY as a search -- it is "
        "in Kometa's movie_only_searches (plex.py:431-436) -- so the show "
        "libtype refuses by name rather than sending a query Plex answers "
        "with the wrong set. Everything else is the `actor` row's note.",
        search_field="director", show_search_field=None,
        search_kinds=("movie",), filterable=True,
    ),
    FilterAttribute(
        "writer", "tag", ("movie",), "unprobed",
        "Plex's `<Writer>` child element. Movie-only per plex.py:431-436, "
        "like `director`; otherwise the `actor` row's note.",
        search_field="writer", show_search_field=None,
        search_kinds=("movie",), filterable=True,
    ),
    FilterAttribute(
        "producer", "tag", ("movie",), "unprobed",
        "Plex's `<Producer>` child element. Movie-only per plex.py:431-436, "
        "like `director`; otherwise the `actor` row's note.",
        search_field="producer", show_search_field=None,
        search_kinds=("movie",), filterable=True,
    ),
```

(`show_search_field=None` on `actor` is deliberate: Kometa's `show_translation` does not rename it, and `field_for("show")` falls through to the bare `search_field` — pin that with the `field_for("show") == "actor"` assertion above.) Update the checksum comment block (`:518-530`) with the new totals and a one-line phase-B note. Re-run to green.

- [ ] **Step 2: Write the failing family-builder tests**

`tests/test_builder_credits_family.py` — model the harness on `tests/test_builder_dynamic.py` (fake section with `listFilterChoices`, fake reconcile capture, a `SmartContext` built the way that module builds one) plus T4's DB seeding. Behaviours to pin (each its own test; write real bodies, these docstrings are the contract):

```python
# 1. depth gates, limit caps: seed credits so actors have counts
#    {"Ann": 6, "Bob": 5, "Cy": 4}; depth=5, limit=1 -> exactly one
#    collection, "Ann"'s, and the family's action strings report the
#    narrowing (2 met depth, capped to 1).
# 2. the emitted URL is the actor-tag search: parse the reconcile capture --
#    the query resolved through the fake listFilterChoices (name -> key),
#    base "any", libtype from the library.
# 3. an empty enumeration REFUSES with the coverage numbers in the message
#    (credits_coverage's attempted/total) and creates nothing.
# 4. fan-out past max_collections refuses with both numbers (facts C5 /
#    the 194 refusal-not-truncation law) and creates nothing.
# 5. the sweep record: run_cache carries the generated-titles set seeded
#    BEFORE any reconcile; a per-key reconcile failure leaves its title in
#    the record (write-failure is not narrowing).
# 6. a person the resolver cannot resolve is contained to that one key:
#    the other collections still reconcile, the refusal names the person.
# 7. director/writer/producer types refuse on a Show library BEFORE any
#    database read (require_library_type first, facts_family's ordering).
# 8. generated_titles returns None when the family refused at family level
#    (fail-closed, dynamic.py's law), and the derived set when it built.
```

Run to see them fail (`No module named ... credits_family`).

- [ ] **Step 3: Implement `src/autoposter/collections/builders/credits_family.py`**

```python
"""``credits_family``: one smart collection per person with enough credits.

The fourth family shape, distinguished the way the others are: ``cs_bucket``
manages SMART collections from a static table, ``dynamic`` SMART collections
from a Plex enumeration, ``facts_family`` LIST collections from a database
enumeration -- and this manages SMART collections from a DATABASE
enumeration, because the question the pack asks ("everyone with at least
`depth` appearances, capped to the `limit` most-credited") needs COUNTS,
which `listFilterChoices` can never answer (values only -- roadmap row 194),
and the counts live in this service's `item_credits` cache (the phase-B
scan). Each person's MEMBERSHIP is still Plex's: a smart collection whose
filter is the person's own tag, resolved through the same
`LibraryTagResolver` every tag search uses -- upstream's own semantic (the
actor TAG, the library's credit data), deliberately NOT a TMDb filmography,
which is a different membership under the same name (row 194's open
question, answered and disclosed in the pack descriptions).

Coverage is honest, not hidden: the enumeration reflects only items the
credits scan has visited, every refusal reports the attempted/total numbers,
and the weekly scan (scheduler.credits_scan_days) is what closes the gap.

Joins the delete sweep through `family_label`/`generated_titles`, the
protocol `builders/dynamic.py` names. The label prefix differs from both
sibling families' so the three sweeps cannot enumerate each other's members.
"""
from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoposter.collections.builders.base import SmartContext, require_library_type
from autoposter.collections.builders.plex_search import LibraryTagResolver
from autoposter.collections.credits import (
    CREDIT_KINDS, credits_coverage, enumerate_credits,
)
from autoposter.collections.dynamic_keys import derive_keys
from autoposter.collections.dynamic_titles import DuplicateFamilyTitle, family_titles
from autoposter.collections.filters import parse_filters
from autoposter.collections.search_url import build_search_url
from autoposter.collections.smart import reconcile_smart_collection

__all__ = [
    "FAMILY_LABEL_PREFIX", "CreditsFamilyBuilder", "CreditsFamilyParams",
    "family_label", "generated_titles",
]

FAMILY_LABEL_PREFIX = "autoposter-credits: "

# Upstream's title shapes: the actor packs title bare names, the three
# movie crews carry the role suffix (the same "<name> (Director)" shape the
# 8c starter set already borrowed from defaults/movie/director.yml).
_TITLE_FORMATS = {
    "actor": "<<key_name>>",
    "director": "<<key_name>> (Director)",
    "writer": "<<key_name>> (Writer)",
    "producer": "<<key_name>> (Producer)",
}
# director/writer/producer are movie-only end to end: their SEARCH is in
# Kometa's movie_only_searches (plex.py:431-436) and their upstream packs are
# defaults/movie/*. actor is both (defaults/both/actor.yml).
_KINDS_FOR_TYPE = {
    "actor": ("Movie", "Show"),
    "director": ("Movie",),
    "writer": ("Movie",),
    "producer": ("Movie",),
}

# dynamic.py:222-231's tuple, verbatim and for its exact reasons (every
# exception class an operator's configuration can cause once it meets a real
# library; exhaustive rather than `Exception` so a genuine bug still reaches
# the engine as a failure). Import the eight classes from the same modules
# dynamic.py imports them from -- copy its import lines, not the module.
_REFUSALS = (
    LibraryTypeMismatch,
    PlexSearchUnavailable,
    SearchAttributeNotAvailable,
    SearchProducedNothing,
    SmartCollectionUnavailable,
    SmartFilterMatchedNothing,
    SortNotAvailable,
    TagValueNotFound,
)


def family_label(definition) -> str:
    return "%s%s" % (FAMILY_LABEL_PREFIX, definition.title)


def _generated_key(label: str) -> str:
    return "credits_family:generated:%s" % label


def generated_titles(run_cache: dict, definition) -> set[str] | None:
    """None is fail-closed -- the family never got as far as deciding -- and
    a set is what it derived this pass; verbatim `builders/dynamic.py`'s
    contract, because `engine._family_state` reads both the same way."""
    return run_cache.get(_generated_key(family_label(definition)))


class CreditsFamilyParams(BaseModel):
    """`data: {depth, limit}` is upstream's own vocabulary for these packs
    (all four transcribed in .superpowers/sdd/p10c-upstream-person.md 2.1);
    the narrowing/titling knobs are the shared family vocabulary
    `DynamicParams`/`FactsFamilyParams` already teach. Absent on purpose:
    `other_name` (a leftovers bucket ORing every sub-threshold person into
    one query is a URL longer than the chunk cap the phase-B probe
    measured), and `minimum_items` (depth IS this family's floor, counted in
    the database before a single Plex call)."""

    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)

    type: str
    depth: int = Field(default=5, ge=1)
    limit: int = Field(default=25, ge=1)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    addons: dict[str, list[str]] = Field(default_factory=dict)
    custom_keys: bool = True
    key_name_override: dict[str, str] = Field(default_factory=dict)
    title_override: dict[str, str] = Field(default_factory=dict)
    remove_prefix: list[str] = Field(default_factory=list)
    remove_suffix: list[str] = Field(default_factory=list)
    title_format: str | None = None
    sort_by: list[str] = Field(default_factory=list)
    max_collections: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def _the_type_must_be_a_credit_kind(self) -> "CreditsFamilyParams":
        if self.type not in CREDIT_KINDS:
            raise ValueError(
                "%r is not a credit kind this service scans. Options: %s"
                % (self.type, ", ".join(CREDIT_KINDS))
            )
        return self


class CreditsFamilyBuilder:
    """A family of smart collections, one per sufficiently-credited person."""

    type_name = "credits_family"
    smart = True
    params_model = CreditsFamilyParams

    # DynamicBuilder's seven (dynamic.py:493-531), verbatim in structure --
    # this definition names a family of smart collections and every refusal
    # holds for the same reasons; only the narrowing advice differs.
    refused_definition_fields = {
        "summary": (
            "this definition names a FAMILY of collections, one per person, "
            "so a single summary could not be the summary of any particular "
            "one of them"
        ),
        "sort": (
            "Plex evaluates each collection's membership live, so there is "
            "no resolved order to set. Use `params.sort_by` for the search's "
            "own order"
        ),
        "limit": (
            "Plex evaluates each collection's membership from a filter, so "
            "there is no resolved list to cap. `params.limit` caps how many "
            "PEOPLE the family builds; `params.depth` is its floor"
        ),
        "sync_mode": (
            "Plex owns these collections' membership; there is nothing for "
            "this service to sync or append to"
        ),
        "item_label": (
            "this service never resolves these collections' members -- Plex "
            "does -- so there is no list of items to label"
        ),
        "tmdb_summary": (
            "this definition names a family of collections, so one borrowed "
            "overview could not be the summary of any particular one of them"
        ),
        "filters": (
            "a `filters:` block narrows a membership this service resolved, "
            "and these are never resolved here -- the items are chosen "
            "inside Plex, by each person's own tag filter. Narrow the family "
            "with `depth:`, `limit:`, `include:` or `exclude:` instead"
        ),
    }

    def family_label(self, definition) -> str:
        return family_label(definition)

    def generated_titles(self, run_cache: dict, definition) -> set[str] | None:
        return generated_titles(run_cache, definition)

    async def apply(self, ctx: SmartContext) -> list[str]:
        definition = ctx.definition
        if definition is None:
            raise ValueError(
                "the 'credits_family' builder builds the family its "
                "definition names, so it cannot run without one"
            )
        params = CreditsFamilyParams.model_validate(definition.params)
        libtype = ctx.library_type.lower()
        actions: list[str] = []

        # Above every read, deliberately: a movie-only type on a show
        # library costs zero database work and zero Plex round trips.
        require_library_type(
            "the 'credits_family' builder's %r type" % params.type,
            ctx.library_type, _KINDS_FOR_TYPE[params.type],
        )

        async with ctx.session.begin_nested():
            counted = await enumerate_credits(
                ctx.session, params.type,
                library=ctx.library, library_type=ctx.library_type,
            )
            attempted, total = await credits_coverage(
                ctx.session, library=ctx.library, library_type=ctx.library_type,
            )

        eligible = [(person, count) for person, count in counted if count >= params.depth]
        capped = eligible[: params.limit]
        if not capped:
            return [
                "%r built nothing: no %s has %d appearance(s) in %r yet. The "
                "credits scan has visited %d of %d item(s) there; the family "
                "fills in as the weekly scan works through the rest "
                "(scheduler.credits_scan_days). Lower `depth:` if the "
                "threshold is the problem"
                % (definition.title, params.type, params.depth, ctx.library,
                   attempted, total)
            ]
        if len(eligible) > len(capped):
            actions.append(
                "%r: %d %s(s) meet depth %d; built the %d most-credited "
                "(`limit`)" % (definition.title, len(eligible), params.type,
                               params.depth, len(capped))
            )

        derived = derive_keys(
            [(person, person) for person, _count in capped],
            include=params.include, exclude=params.exclude,
            addons=params.addons, custom_keys=params.custom_keys,
        )
        try:
            titled = family_titles(
                derived,
                library_type=ctx.library_type,
                title_format=params.title_format or _TITLE_FORMATS[params.type],
                key_name_override=params.key_name_override,
                title_override=params.title_override,
                remove_prefix=params.remove_prefix,
                remove_suffix=params.remove_suffix,
                other_name=None,
            )
        except DuplicateFamilyTitle as refusal:
            return actions + ["refused %r: %s" % (definition.title, refusal)]
        if not titled:
            return actions + [
                "%r built nothing: every eligible %s was excluded. Widen "
                "`include:`, or remove the definition"
                % (definition.title, params.type)
            ]
        if len(titled) > params.max_collections:
            return actions + [
                "%r built nothing: this would create %d collections in %r "
                "and `max_collections` is %d. Narrow with `depth:`/`limit:`/"
                "`exclude:`, or raise `max_collections` past %d if that is "
                "really what you want"
                % (definition.title, len(titled), ctx.library,
                   params.max_collections, len(titled))
            ]

        # The sweep's record -- seeded with every derived title BEFORE any
        # reconcile, dynamic.py's law: a write failure is not narrowing.
        generated: set[str] = {unit.title for unit in titled}
        ctx.run_cache[_generated_key(family_label(definition))] = generated

        settings = definition.model_copy(
            update={"labels": [*definition.labels, family_label(definition)]}
        )
        listing = ctx.listing() if ctx.listing is not None else None
        resolver = LibraryTagResolver(ctx, ctx.section, libtype)
        collections = ctx.config.collections

        for unit in titled:
            try:
                parsed = parse_filters(
                    {params.type: list(unit.values)},
                    field="params", searching=True, base="any",
                )
                url = build_search_url(
                    parsed, libtype=libtype, sort_by=params.sort_by,
                    limit=None, resolve_tag=resolver,
                )
                actions += await reconcile_smart_collection(
                    ctx.session, ctx.section, ctx.library, ctx.library_type,
                    unit.title, url, ctx.label,
                    summary=None,
                    dry_run=ctx.dry_run,
                    existing=listing,
                    adopt=collections.adopt,
                    adopt_from=collections.adopt_from,
                    adopt_removes_prior_label=collections.adopt_removes_prior_label,
                    protect_labels=collections.protect_labels,
                    http=ctx.http,
                    config=ctx.config,
                    settings=settings,
                    sort_prefix=ctx.sort_prefix,
                )
            except _REFUSALS as refusal:
                # One person's problem stays one person's: an unresolvable
                # tag or a filter Plex refuses must not stop the other
                # twenty-four collections being managed.
                actions.append("refused %r: %s" % (unit.title, refusal))
        return actions
```

Fill the two transcription markers (`_REFUSALS`, `refused_definition_fields`) from `builders/dynamic.py` verbatim — they are deliberate copies, and the test for the refused fields is whatever `tests/test_builder_dynamic.py` pins for DynamicBuilder's, mirrored. Add a `ValueError` guard for `ctx.session is None` before the `begin_nested` (message naming the builder, `facts_family`'s wording). Register in `builders/__init__.py`:

```python
from autoposter.collections.builders.credits_family import CreditsFamilyBuilder
register(CreditsFamilyBuilder())
```

- [ ] **Step 4: Run the family tests to green**

```bash
docker compose -p pphb5 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_builder_credits_family.py tests/test_collection_filters.py tests/test_collection_search_oracle.py tests/test_collection_search_url.py 2>&1 | tee /app/.superpowers/run-pphb5-green1.log; echo EXIT=\$?"
```

Expected: all pass (the search oracle must not notice four added rows — if it pins the table's searchable set, update its pinned list in the same spirit as Step 1's checksums).

- [ ] **Step 5: Flip the four packs** (`src/autoposter/collections/catalog.py:1879-1901`)

Replace the GATED generator with READY presets, one `credits_family` collection each. Keep `_PERSON_PACKS` as the data; change the tuple to carry the params type:

```python
_PERSON_PACKS = (
    ("people_top_actors", "Top actors", "defaults/both/actor.yml", "actor",
     "the twenty-five actors with the most appearances in the library", _BOTH),
    ("people_top_directors", "Top directors", "defaults/movie/director.yml", "director",
     "the twenty-five directors with the most films in the library", _MOVIE),
    ("people_top_writers", "Top writers", "defaults/movie/writer.yml", "writer",
     "the twenty-five writers with the most films in the library", _MOVIE),
    ("people_top_producers", "Top producers", "defaults/movie/producer.yml", "producer",
     "the twenty-five producers with the most films in the library", _MOVIE),
)
```

and the generated presets become:

```python
    Preset(
        key=key,
        category="people",
        name=name,
        description=(
            "One smart collection per person: %s. `depth: 5, limit: 25` is "
            "upstream's own data block, and the membership is upstream's own "
            "semantic -- each collection is a Plex search on the person's "
            "TAG, the library's credit data, NOT a TMDb filmography (which "
            "credits people the library's files do not name; the two are "
            "different memberships under the same name, and this pack "
            "chooses the tag on purpose). The people are counted from this "
            "service's credits cache, which the weekly scan fills "
            "(scheduler.credits_scan_days): on a library the scan has not "
            "finished, the family is smaller than it will be -- correct, "
            "incomplete, and converging, and the pass's own report says so "
            "in numbers." % what
        ),
        kometa_source=source,
        library_types=library_types,
        collections=(
            PresetCollection(
                title=name,
                builder="credits_family",
                params=(("type", kind), ("depth", 5), ("limit", 25)),
            ),
        ),
    )
    for key, name, source, kind, what, library_types in _PERSON_PACKS
```

Update `tests/test_collection_catalog.py`: the four keys leave whatever GATED/readiness pins exist and join the READY assertions; the `gated_row` references to `PERSON_SCAN_ROW` for these four go. (The `people_directors` starter preset is untouched.) If any catalog test pins pack counts by category or readiness totals, recompute them — the file's own failures name each.

- [ ] **Step 6: Full neighbourhood + golden gate + commit**

```bash
docker compose -p pphb5b -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_collection_catalog.py tests/test_builder_dynamic.py tests/test_collection_config.py tests/test_api_collections_builders.py 2>&1 | tee /app/.superpowers/run-pphb5-green2.log; echo EXIT=\$?"
git diff --stat tests/fixtures/collections/golden_port.json
git add src/autoposter/collections/filters.py src/autoposter/collections/builders/credits_family.py src/autoposter/collections/builders/__init__.py src/autoposter/collections/catalog.py tests/test_collection_filters.py tests/test_builder_credits_family.py tests/test_collection_catalog.py
git commit --no-gpg-sign -m "feat(people): actor/director/writer/producer search rows and the counted credits_family -- four person packs flip READY"
```

(If the search-oracle or API-builders tests needed pinned-list updates in Steps 4-5, stage those files too — by name.)

---

### Task 6: Row 91 — the `plex_collectionless` builder

> **CONFIRMED-BY-T1: D2, D3 only.** Not credit-gated: 9a's probe F already proved `<Collection>` tags ride the batch (132 Collection tags in the chunk=200 response), and T1's probe (a) re-verifies for the show library. The controller re-checks only that D2/D3 recorded no surprise (a chunk cap so low or show-library timing so bad that a whole-library enrichment is unreasonable — not expected).

**Files:**
- Create: `src/autoposter/collections/builders/collectionless.py`
- Modify: `src/autoposter/collections/builders/__init__.py` (import + `register(PlexCollectionlessBuilder())`)
- Test: `tests/test_builder_collectionless.py`

**Interfaces:**
- Consumes: T2's `ensure_tags(section, run_cache, rating_keys)` and `ItemTags.collections`; `ctx.sources.plex` (`PlexSectionAccess.section()` / `.owned_index()`, `sources_bundle.py:49-77`); `plex_trivial.PlexLibraryUnavailable`.
- Produces: `builder: plex_collectionless` with params `{exclude, exclude_prefix}`.

**Why this is now an ordinary `Builder`:** row 91's premise correction — Kometa's version reads `item.collections` per item, one FORCED reload per library item (the exact trap 9a forbade). Here the resolved set genuinely IS the whole library, so the builder reads the engine's owned index (zero extra walks, `plex_all`'s discipline) and pays one `ceil(N/chunk)` batched enrichment through the run cache — which any tier-2 `filters:` definition in the same pass then reuses for free.

- [ ] **Step 1: Write the failing tests**

`tests/test_builder_collectionless.py`:

```python
"""plex_collectionless: membership from the batched read, never a reload."""
import pytest

from autoposter.collections.builders.base import BuilderContext
from autoposter.collections.builders.collectionless import (
    CollectionlessRefused, PlexCollectionlessBuilder,
)
from autoposter.collections.builders.plex_trivial import PlexLibraryUnavailable
from autoposter.collections.builders.sources_bundle import PlexSectionAccess, SourceClients


class FakeTag:
    def __init__(self, tag):
        self.tag = tag


class FakeItem:
    def __init__(self, rating_key, collections=()):
        self.ratingKey = rating_key
        self.genres = []
        self.labels = []
        self.collections = [FakeTag(c) for c in collections]
        self.media = []


class FakeSection:
    def __init__(self, items, answer_all=True):
        self._items = {int(i.ratingKey): i for i in items}
        self.answer_all = answer_all
        self.calls = []

    def fetchItems(self, ekey):
        self.calls.append(list(ekey))
        found = [self._items[k] for k in ekey if k in self._items]
        return found if self.answer_all else found[:-1]


def _context(items, params=None, section=None, run_cache=None):
    section = section or FakeSection(items)
    index = {"plex": {str(i.ratingKey): i for i in items}}
    return section, BuilderContext(
        library="Movies", library_type="Movie",
        config=params or {},
        run_cache=run_cache if run_cache is not None else {},
        sources=SourceClients(plex=PlexSectionAccess(section, lambda: index)),
    )


async def test_membership_is_the_items_in_no_collection_in_index_order():
    items = [FakeItem("1"), FakeItem("2", ["Alien Collection"]), FakeItem("3")]
    section, ctx = _context(items)
    result = await PlexCollectionlessBuilder().build(ctx)
    assert result.ids == [("plex", "1"), ("plex", "3")]


async def test_excluded_collections_do_not_count():
    items = [FakeItem("1", ["Decade: 1980s"]),
             FakeItem("2", ["Decade: 1980s", "Alien Collection"]),
             FakeItem("3", ["Overlay"])]
    section, ctx = _context(items, params={
        "exclude": ["Overlay"], "exclude_prefix": ["Decade: "],
    })
    result = await PlexCollectionlessBuilder().build(ctx)
    assert result.ids == [("plex", "1"), ("plex", "3")]


async def test_one_batched_fetch_for_the_whole_library():
    items = [FakeItem(str(k)) for k in range(1, 6)]
    section, ctx = _context(items)
    await PlexCollectionlessBuilder().build(ctx)
    assert len(section.calls) == 1 and section.calls[0] == [1, 2, 3, 4, 5]


async def test_a_second_build_reuses_the_run_cache():
    items = [FakeItem("1")]
    run_cache = {}
    section, ctx = _context(items, run_cache=run_cache)
    await PlexCollectionlessBuilder().build(ctx)
    await PlexCollectionlessBuilder().build(ctx)
    assert len(section.calls) == 1


async def test_an_unanswered_item_refuses_the_whole_membership():
    items = [FakeItem("1"), FakeItem("2")]
    section = FakeSection(items, answer_all=False)
    section, ctx = _context(items, section=section)
    with pytest.raises(CollectionlessRefused):
        await PlexCollectionlessBuilder().build(ctx)


async def test_no_library_accessor_raises_by_name():
    ctx = BuilderContext(library="Movies", library_type="Movie")
    with pytest.raises(PlexLibraryUnavailable):
        await PlexCollectionlessBuilder().build(ctx)
```

(Match the suite's async-test convention as in T2. Note `_context` returns the section actually used — the unanswered-item test passes its own.) Run (`pphb6`) to see them fail on the missing module.

- [ ] **Step 2: Implement `src/autoposter/collections/builders/collectionless.py`**

```python
"""``plex_collectionless``: the items in no collection on the server (row 91).

Kometa's is a plain list builder that reads `item.collections` per item --
one forced reload per library item per pass (`modules/plex.py`, the
collectionless branch), the exact trap phase 9a exists to avoid. This one
reads the engine's owned index (no walk of its own, `plex_all`'s
discipline) and ONE batched enrichment for the library, shared through the
run cache with every tier-2 filter definition in the pass.

`exclude` / `exclude_prefix` are Kometa's collectionless knobs: a membership
named there does not count against an item, so "collectionless" can mean
"in nothing but the Decade: buckets". Matching is exact for `exclude` and
`str.startswith` for `exclude_prefix`, both case-sensitive -- Plex
collection titles are canonical strings, and a looser match would silently
widen what gets ignored. (Our reading of upstream's semantics; the
startswith half is upstream's own idiom, the case-sensitivity is stated
here rather than checked against a fetch -- NOT KOMETA-VERIFIED.)
"""
import logging

from pydantic import BaseModel, ConfigDict, Field

from autoposter.collections.builders.base import BuilderContext, BuilderResult
from autoposter.collections.builders.plex_trivial import PlexLibraryUnavailable
from autoposter.collections.enrichment import ensure_tags

logger = logging.getLogger(__name__)

__all__ = ["CollectionlessRefused", "PlexCollectionlessBuilder", "PlexCollectionlessParams"]


class CollectionlessRefused(Exception):
    """The enrichment could not answer for every owned item.

    Raised, never degraded: an item whose collections we could not read
    would otherwise be indistinguishable from an item in none, and this
    builder exists to assert the difference. The engine contains it as one
    dead source (class-name-only in the report), the pass continues.
    """


class PlexCollectionlessParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Collection titles that do not count as "being in a collection".
    exclude: list[str] = Field(default_factory=list)
    # Title prefixes that do not count -- the family-bucket idiom
    # ("Decade: ", "autoposter-facts: ").
    exclude_prefix: list[str] = Field(default_factory=list)


class PlexCollectionlessBuilder:
    """Every owned item whose (unexcluded) collection list is empty."""

    type_name = "plex_collectionless"
    params_model = PlexCollectionlessParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = PlexCollectionlessParams.model_validate(ctx.config)
        access = ctx.sources.plex
        if access is None:
            raise PlexLibraryUnavailable(
                "the 'plex_collectionless' builder reads the library it is "
                "running against, and this context carries no library accessor"
            )
        rating_keys = list(access.owned_index()["plex"])
        tags = await ensure_tags(access.section(), ctx.run_cache, rating_keys)
        missing = [key for key in rating_keys if key not in tags]
        if missing:
            raise CollectionlessRefused(
                "the batched read answered for %d of %d item(s); an item "
                "whose collections cannot be read is not known to be "
                "collectionless, so nothing was built. Re-run the pass; if "
                "this repeats, items may be leaving the library mid-pass"
                % (len(rating_keys) - len(missing), len(rating_keys))
            )
        excluded = set(params.exclude)
        prefixes = tuple(params.exclude_prefix)

        def counts(title: str) -> bool:
            """Whether this membership counts against the item."""
            if title in excluded:
                return False
            return not any(title.startswith(prefix) for prefix in prefixes)

        members = [
            key for key in rating_keys
            if not any(counts(title) for title in tags[key].collections)
        ]
        logger.debug(
            "plex_collectionless: %d of %d item(s) in no (counted) collection",
            len(members), len(rating_keys),
        )
        return BuilderResult(ids=[("plex", key) for key in members])
```

Register in `builders/__init__.py`:

```python
from autoposter.collections.builders.collectionless import PlexCollectionlessBuilder
register(PlexCollectionlessBuilder())
```

- [ ] **Step 3: Green, neighbourhood, golden gate, commit**

```bash
docker compose -p pphb6 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_builder_collectionless.py tests/test_builder_engine.py tests/test_api_collections_builders.py tests/test_collection_config.py 2>&1 | tee /app/.superpowers/run-pphb6-green1.log; echo EXIT=\$?"
git diff --stat tests/fixtures/collections/golden_port.json
git add src/autoposter/collections/builders/collectionless.py src/autoposter/collections/builders/__init__.py tests/test_builder_collectionless.py
git commit --no-gpg-sign -m "feat(collections): plex_collectionless builder on the batched read -- row 91"
```

(If the API builders test enumerates registry entries, update its pinned list — by name, in the same commit.)

---

### Task 7: The wrap — row 198's counting read, the 180/175 carried verdicts, the bookkeeping

> **CONFIRMED-BY-T1: D4, D5.** The row-198 commit (Step 1) ships ONLY if D5 says CONFIRMED — otherwise skip Step 1 entirely and the row keeps its honest "unverified reading" text plus one sentence recording what the probe actually saw. Steps 2-3 branch on D4 exactly as written. The bookkeeping in Steps 4-6 runs regardless, describing whatever actually shipped (if T4/T5 were parked BLOCKED-FOR-ADJUDICATION, rows 194/169 are re-pointed to the adjudication rather than closed — honesty over momentum).

**Files:**
- Modify: `src/autoposter/collections/smart.py` (`count_matches`, `:107-128`) + `tests/test_collection_smart.py` (+ one pin in `tests/test_plexapi_collection_contract.py`)
- Modify: `src/autoposter/collections/filters.py` (rows `plays` `:777`, `last_played` `:796`; new `user_rating` row; checksums), `src/autoposter/collections/filter_values.py` (`_LISTING_ATTRIBS`, D4 branch only) + their tests
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, `src/autoposter/collections/dynamic_types.py` (docstring), `src/autoposter/collections/catalog.py` (row comments `:694-701`)
- Create (uncommitted): `.superpowers/sdd/p-phaseb-pr-body.md`

- [ ] **Step 1 (only if D5=CONFIRMED): `count_matches` stops materialising the items — its own commit**

Test first, in `tests/test_collection_smart.py`: give the module's fake section a `_server` whose `query(path, headers=...)` records the headers and returns an element with `totalSize="4123"`; assert `count_matches` returns 4123, sends `X-Plex-Container-Size: 0`, and never calls `fetchItems`; keep the existing failure-wrap test (any exception → `SmartCollectionUnavailable`, class-name-only) and add: a response with no `totalSize` attrib is a failure, not a zero. Then:

```python
def count_matches(section, url: str) -> int:
    """How many items ``url`` matches right now. Zero is an answer, not a fault.

    A container-size-0 read, not a fetch (roadmap row 198, closed by the
    phase-B probe: Plex answers the same search URL with a ``totalSize``
    attrib when asked for zero items -- docs/research/plex-batch-probe/
    README.md, probe e). Validating a filter that matches four thousand
    items no longer pulls four thousand items to learn "more than none";
    ``require_matches`` and the dynamic engine's ``minimum_items`` both
    inherit the cheap read through this one function.

    The catch is blanket and class-name-only, as before and for the same
    reason -- and a response with no ``totalSize`` is a failure too, never
    a zero: zero MATCHES is an answer, an unreadable count is not.
    """
    try:
        data = section._server.query(
            "/library/sections/%s/all%s" % (section.key, url),
            headers={"X-Plex-Container-Start": "0", "X-Plex-Container-Size": "0"},
        )
        total = data.attrib.get("totalSize")
        if total is None:
            raise ValueError("no totalSize attrib on the container response")
        return int(total)
    except Exception as error:  # class name only, never the message
        raise SmartCollectionUnavailable(
            "Plex would not answer this smart filter: %s" % type(error).__name__
        ) from None
```

`section._server` joins the pinned plexapi internals: add one assertion to `tests/test_plexapi_collection_contract.py` beside the `_uriRoot` pin (`LibrarySection` instances carry `_server`, and `PlexServer.query` accepts `headers=`). Run the smart tests green, then:

```bash
git add src/autoposter/collections/smart.py tests/test_collection_smart.py tests/test_plexapi_collection_contract.py
git commit --no-gpg-sign -m "fix(smart): count_matches reads totalSize at container-size 0 -- row 198"
```

- [ ] **Step 2 (branch on D4): rows 180 (`plays`/`last_played`) and 175 (`user_rating`)**

**D4 = ALL-PRESENT for viewCount/lastViewedAt** (every listing item carries the attrib, unplayed items included): move both rows' `source` to `"listing"`, append to each note: `" PHASE B: the phase-B probe walked both listings and found the attrib on every item (docs/research/plex-batch-probe/README.md, probe d), so it ships listing-tier. Still PER-ACCOUNT: the value is the pass token's account's."` Add both to `filter_values._LISTING_ATTRIBS` (`"plays": "viewCount"`, `"last_played": "lastViewedAt"`). Checksums: sources become `11 listing / 5 tier2-batched / 1 tier2-deferred / 5 unprobed / 3 search-only` (before Step 2's user_rating row lands — recompute after it).

**D4 = SPARSE or ABSENT**: the rows keep `"unprobed"`... no — they now HAVE a verdict, so honesty requires recording it without shipping wrongly: append to each note `" PHASE B VERDICT: the listing carries the attrib on <N> of <total> items (played items only), so a listing accessor would read 'never played' as MISSING -- and the int/date missing rule excludes missing unconditionally, which is not Kometa's zero-plays semantics. Filed rather than shipped wrong; the rows stay searches."` and leave `source` as is (the tier stays `unprobed`'s refusal path; the note now carries the real numbers). Roadmap row 180 is then re-filed, not closed (Step 4).

**Row 175, either branch:** add the `user_rating` row after the phase-B people rows (mechanically `audience_rating` again — roadmap 175's own words):

```python
    FilterAttribute(
        "user_rating", "float", _BOTH, "listing",  # or "unprobed" -- see D4
        "Plex's `userRating` -- the star rating belonging to WHOSE TOKEN THE "
        "PASS AUTHENTICATED AS, not the library's aggregate (roadmap row 175: "
        "the row's note has to say whose rating it is, or an operator reads "
        "'user rating' as the library's and gets their own). Mechanically the "
        "`audience_rating` row again: a float with the five float modifiers. "
        "PHASE B probe (d) verdict: <transcribe the presence numbers>. An "
        "item the account has not rated has no value, and the float missing "
        "rule excludes it unconditionally -- which is Kometa's own None "
        "handling, so sparse presence ships honestly.",
        search_field="userRating", show_search_field="show.userRating",
        search_kinds=_BOTH, filterable=True,
    ),
```

Tier: `"listing"` (plus a `_LISTING_ATTRIBS` entry `"user_rating": "userRating"`) if probe (d) found the attrib present on ANY items — sparse is fine here because missing-excludes IS the correct semantics for an unrated item; `"unprobed"` only if probe (d) never measured it. Final checksums (**26 rows**, D4-ALL-PRESENT branch): types `13 tag / 1 str / 3 int / 3 float / 3 date / 1 duration / 2 bool`; sources `12 listing / 5 tier2-batched / 1 tier2-deferred / 5 unprobed / 3 search-only`; kinds `15 both / 10 movie / 1 show`; search_kinds `18 both / 7 movie / 1 show`. (Sparse-D4 branch: `plays`/`last_played` stay unprobed → `10 listing / … / 7 unprobed`. Recompute against what actually moved and write the arithmetic into the checksum comment.) Tests: update `tests/test_collection_filters.py` totals and, on the listing branches, `tests/test_collection_filter_values.py`'s accessor-map pin. TDD order as ever: totals red, rows in, totals green.

```bash
docker compose -p pphb7 -f docker-compose.yml -f .superpowers/isolated-db.yml run --rm test sh -c "pytest -q tests/test_collection_filters.py tests/test_collection_filter_values.py tests/test_collection_config.py tests/test_collection_search_oracle.py 2>&1 | tee /app/.superpowers/run-pphb7-green1.log; echo EXIT=\$?"
git add src/autoposter/collections/filters.py src/autoposter/collections/filter_values.py tests/test_collection_filters.py tests/test_collection_filter_values.py
git commit --no-gpg-sign -m "feat(filters): user_rating row and the plays/last_played verdict -- rows 175/180"
```

- [ ] **Step 3: Docstring/comment bookkeeping in code**

- `src/autoposter/collections/dynamic_types.py` docstring, the people bullet (`:42-51`): re-point — the people types are still deliberately absent from THIS table, but the reason is now "they ship as `builder: credits_family`, enumerated from this service's `item_credits` cache with real counts (roadmap row 194, phase B), the same reason the facts families are not rows here" (or, if T5 was parked: update the citation from "the rows 155/180 budget family" to row 197's shipped batch + the standing adjudication).
- `src/autoposter/collections/catalog.py:694-701`: rewrite the `PERSON_SCAN_ROW` comment to the shipped state (the scan exists, the four packs flipped) or the parked state — never leave the pre-phase text.
- `src/autoposter/collections/filter_values.py` module docstring: one final read-through — the deferred story must now say five batched + one stranded, and the probe history must still be intact.

- [ ] **Step 4: The roadmap** (`docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` — append/in-row edits only, never restructure)

Per facts C7, in-row, each edit one bolded sentence-block in the row's own voice, citing `docs/research/plex-batch-probe/README.md` where a probe decided it:

- **197**: SHIPPED (phase B) — the batched read (chunked `fetchItems` list-of-ints, per-run enrichment cache scoped to resolved sets, `ceil(N/chunk)` pinned by test), the cache-shape decision taken as written (a DEDICATED `item_credits` composite-PK table, not `item_facts`), the credits scan + weekly job. Row closes.
- **155**: five of six ship as `tier2-batched` `filters:` attributes; `network`'s (b)-half verdict recorded in-row (distinct-source-or-no-meaning; nothing shipped false). Row closes.
- **169**: the four BY_NAME rows + `listFilterChoices` resolution ship (hubSearch NOT built — say why, C5); movie-only gates per plex.py:431-436. Closes — or re-points at the D7 adjudication if parked.
- **194**: the scan, the counted enumeration, `credits_family`, the four packs READY; the open question answered (Plex tag semantics, disclosed in pack descriptions); `tmdb_popular_people` stays filed (nothing here changed its verdict). Closes — or re-points at the D1 adjudication if parked.
- **91**: `plex_collectionless` shipped as an ordinary Builder on the batched read. Closes.
- **180**: closes to `listing` with the probe-(d) numbers, or re-files with them (Step 2's branch); the per-account sentence is in the row already — point at the shipped note.
- **175**: closes (the row + the whose-rating sentence shipped).
- **198**: closes on the container-size read (D5), or records the probe's negative finding and stays.
- **The fifth carry**: 9c's unbounded `require_matches` (branch-review-9c.md:144; counted as its fifth deferral by row 198's own text) — with Step 1 shipped, `require_matches` inherits the counting read through `count_matches` and the carry is DISCHARGED: say so in row 198's closing text. If D5 failed, the carry stands with the probe's finding named.

- [ ] **Step 5: The PR body (uncommitted) and the full suite**

Write `.superpowers/sdd/p-phaseb-pr-body.md`: title `feat: phase B -- batched Plex read, tier-2 filters, credits cache (rows 197/155/169/175/180/91/194/198)`; sections: What shipped (per task, one paragraph each), The probes (five, one line each, decision table verdicts), Rows closed/re-filed (the C7 list with outcomes), Test evidence (the pphb log names and final counts). No AI attribution, no URLs/hosts/tokens. Verify it stays untracked:

```bash
git check-ignore .superpowers/sdd/p-phaseb-pr-body.md && echo IGNORED-OK
```

Then the full suite, detached (Global Constraint 3's long-run shape):

```bash
docker compose -p pphb7 -f docker-compose.yml -f .superpowers/isolated-db.yml run -d --name pphb7-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pphb7-full.log"
docker wait pphb7-full
docker cp pphb7-full:/app/.superpowers/run-pphb7-full.log ./run-pphb7-full.log
tail -5 ./run-pphb7-full.log
docker compose -p pphb7 down
```

Expected: everything passes (the known `test_updated_at_advances_on_second_persist_facts` timestamp flake is the only tolerated failure, and only if it matches that exact test — re-run it alone to confirm flake, never wave through anything else). Then:

```bash
git diff --stat tests/fixtures/collections/golden_port.json
```

Expected: empty.

- [ ] **Step 6: Final commit**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md src/autoposter/collections/dynamic_types.py src/autoposter/collections/catalog.py src/autoposter/collections/filter_values.py
git commit --no-gpg-sign -m "docs(roadmap): phase B wrap -- 197/155/91 closed, 169/194/175/180/198 per their probes"
```

**Do NOT push. Do NOT create a PR.** The controller runs the whole-branch review first and owns both.
