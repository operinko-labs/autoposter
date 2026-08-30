# Hardening Sweep 1 — Redaction, Refusal Caching, the Flake, and the ParseError Rows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close roadmap rows 115, 117, 136, 188, 147 and 205 (and close or re-file 195 on loop evidence) — one coherent theme: failures and secrets are surfaced honestly and never leak or linger.

**Architecture:** Three code tasks grouped by code area plus a wrap. Task 1 is the redaction quartet (115/117/136/188): one scrub seam where the served log surface is assembled (`api/logs.py::LogBuffer.emit`) covers row 117's three `exc_info` sites at once, and one local class-name-only fix at the reconcile rollback boundary (`collections/service.py:395`) closes rows 136 and 188 — which cite the SAME current line — while row 115 is verified already-delivered and closed as paperwork. Task 2 is the provider-refusal pair (147/205): `fetch_json` gains a `cacheable` predicate so MDBList's 200-with-a-limit-body never becomes a cached answer (mirroring `tmdb_budget`'s never-cache-a-refusal law), and `ElementTree.ParseError` joins the catch tuple at BOTH stream-read sites in one commit with the stale comment corrected. Task 3 is row 195's reproduce-first loop (200 iterations, instrumented, decision tree — never blind-stabilize). Task 4 is the wrap: roadmap closes/re-files, PR body (gitignored, uncommitted), full suite stated-then-measured. NO push, NO PR.

**Tech Stack:** Python 3.13/3.14, pytest (+xdist) in the compose test container, httpx MockTransport for provider fakes, plexapi 4.18.2 (read, not modified).

**Binding requirements:** `.superpowers/sdd/p-hardening-facts.md` (C1–C7, adjudicated 2026-08-31). Where this plan and that file could ever disagree, that file wins.

## Global Constraints

Every task's requirements implicitly include this section.

1. **Scope (facts C1).** SEVEN rows IN: 115, 117, 136, 188, 147, 195, 205. Everything else is OUT — explicitly the test/CI hygiene family (107/109/118/131/142/152/167), the correctness-quirks family (144/154/159/168/186/187), the small features, row 202, and rows 203/204's remaining halves. Honest scope beats a mega-sweep. Touch nothing outside the rows' named surfaces.
2. **The redaction law (facts C2, verbatim):** operator URLs never in logs/errors/events; transport errors class-name-only; tokens/keys never printed. The four redaction rows are instances of ONE law — the plan may build ONE redaction helper/seam if the rows' fixes converge on it naturally, but MUST NOT invent an abstraction the four call sites don't actually share (YAGNI; the reviewer will check). This plan's convergence analysis is in the section below; the verdict is TWO seams, not one abstraction.
3. **Row 147's fix shape (facts C3):** the MDBList client must never cache a limit/error body (mirror tmdb_budget's refusal-never-cached law and its tests); recovery latency measured-or-reasoned in the row's close.
4. **Row 195's discipline (facts C4):** reproduce FIRST (the loop), understand the mechanism, then fix the ASSERTION or the ROOT depending on what the reproduction shows — never blind-stabilize. If it cannot be reproduced in a bounded loop (200 iterations), the row re-files with that evidence.
5. **Row 205 (facts C5):** both sites in one commit; the catch tuples gain ParseError/SyntaxError per the row's named mechanism; plex_search.py's stale comment (:462-464 at this tree) corrects in the same commit.
6. **Container discipline (the standing recipe).** Unique compose project per task (`phard1` … `phard4`), always with the `.superpowers/isolated-db.yml` overlay. Tee output to a path under `/app/.superpowers/` inside the container — never rely on streamed stdout (it is filtered, and `--rm` deletes the container before it can be re-read). A long run (the full suite, the Task 3 loop) uses **no `--rm`**, is started detached with `run -d --name <project>-<label>`, and is waited on / polled from the host; the log is then read from the host. Teardown is `docker compose -p <project> down` — **never** `down -v`. Two pytest commands against the same compose project are unsafe — keep unique `-p` names per task. Execution is main-tree; if a worktree is nonetheless used, any `run-*.log` written inside it is copied out BEFORE the worktree is pruned.
7. **Commits.** Conventional messages, `--no-gpg-sign`, staged **by name** (never `git add -A`). No `Co-Authored-By`, no AI attribution of any kind, in commits or the PR body.
8. **Artifacts.** Phase artifacts under `.superpowers/sdd/` use the `p-hard1-` prefix; run logs are `.superpowers/run-p-hard1-*.log`. The PR body goes to `.superpowers/sdd/p-hard1-pr-body.md` — `.superpowers/` is gitignored and the file stays uncommitted. **Push and PR creation are NOT in this plan**: the controller does both after the whole-branch review.
9. **Branch (facts C7).** `fix/hardening-sweep-1` from `origin/main` AFTER PR #102 merges — or from #102's tip (`feat/location-names`, 86f55d4) if the gate is slow, in which case second-to-merge rebases (the standing drill). Verified with a **content probe** (never a sha probe): `grep -q '^| 205 ' docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` must succeed on the cut branch — row 205 exists only in #102's content, and this sweep closes it.
10. **RED-first everywhere.** These are exactly the rows where a test that never went red proves nothing. Every fix's test is run and SEEN to fail before the implementation lands. For row 195 the reproduced loop failure itself is the red.

## The Convergence Analysis (constraint 2's required verdict)

The four instances of the one law, verified at this tree:

| # | Site (current line) | Leak path | Row(s) |
| --- | --- | --- | --- |
| 1 | `src/autoposter/providers/ladder.py:81` | `logger.warning(..., exc_info=True)` → traceback's last line (httpx embeds the full URL; Fanart's `api_key` rides the query string) → `LogBuffer` → `/api/logs` + `/api/logs/stream` | 117 |
| 2 | `src/autoposter/api/candidates.py:200-203` | same `exc_info` pattern (the response body is already class-name-only; the log surface is not) | 117 |
| 3 | `src/autoposter/api/candidates.py:420-423` | same | 117 |
| 4 | `src/autoposter/collections/service.py:395` | `LibraryOutcome(error=str(error))` → `ReconcileResult.summary`/`.detail` → `CollectionsPassFailed` → `scheduled_runs.last_detail` (core.py:169/181, served by `/api/snapshots` — snapshots.py:123) and the job notification (core.py:195) | 136 + 188 |

**Rows 136 and 188 cite the SAME site.** Row 136's `service.py:274` has drifted across the five merged phases; at this tree the only `error=str(error)` assignment is `service.py:395` — exactly the line row 188 cites. One fix closes both rows.

**Verdict: instances 1–3 genuinely converge; instance 4 does not join them.** Sites 1–3 leak through one shared surface — the in-memory log buffer that `/api/logs` and the stream serve — and row 117's own remedy ("scrub `api_key`-style query params from logged exception text") lands naturally at the single point where that surface's text is assembled: `LogBuffer.emit`. One `_scrub` there fixes all three sites (and any future `exc_info` site) without touching them; the pod's stdout keeps the full traceback under the repo's host-only rule. Instance 4 is not a log line at all — it is a data field whose consumers (the status pill's detail, a notification) want a short honest marker, not a scrubbed traceback — so it gets the class-name-only treatment the engine and the preview endpoint (`api/collections_builders.py::_library_failure`) already apply, locally, at the rollback boundary. **No abstraction spans all four sites — building one would be the YAGNI violation constraint 2 forbids.** Two seams: the buffer scrub (three instances), the rollback boundary (one instance).

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `src/autoposter/api/logs.py` | **Modify.** `_scrub` + credential-param regex; `emit` scrubs the formatted message before it enters the buffer/stream | T1 |
| `src/autoposter/collections/service.py` | **Modify.** `:395` records `type(error).__name__` instead of `str(error)` | T1 |
| `tests/test_api_logs.py` | **Modify.** Two RED tests: traceback scrub, plain-message scrub | T1 |
| `tests/test_scheduler_collections_job.py` | **Modify.** `BreaksWithATokenisedUrl` fake + two RED tests (service surface, job/last_detail surface) | T1 |
| `src/autoposter/providers/fetch.py` | **Modify.** `fetch_json` gains keyword-only `cacheable` predicate (default None = today's behaviour) | T2 |
| `src/autoposter/facts/mdblist.py` | **Modify.** `_is_limit_body`; both call sites pass `cacheable`; `list_items` docstring's stale paragraph rewritten | T2 |
| `tests/test_mdblist.py` | **Modify.** Three RED tests: limit body never cached (list + rating), rollover recovery | T2 |
| `src/autoposter/collections/enrichment.py` | **Modify.** Catch tuple gains `ElementTree.ParseError`; comment extended | T2 |
| `src/autoposter/collections/builders/plex_search.py` | **Modify.** Same tuple at `:472`; the false comment clause at `:462-464` corrected — same commit as enrichment (C5) | T2 |
| `tests/test_collection_enrichment.py` | **Modify.** Parametrize gains `ElementTree.ParseError` (RED) | T2 |
| `tests/test_builder_plex_search.py` | **Modify.** New RED test: ParseError wrapped class-name-only | T2 |
| `tests/test_pipeline.py` | **Uncommitted diagnostic patch during T3's loop; then either reverted (no repro / root fix) or the assertion corrected (evidence branch B)** | T3 |
| `src/autoposter/render/pipeline.py` | **Modify ONLY on T3 evidence branch C** (`set_` → `func.clock_timestamp()`) | T3 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modify.** Rows 115/117/136/188/147/205 closed; 195 closed or re-filed per T3 | T4 |
| `.superpowers/sdd/p-hard1-pr-body.md` | **Create (gitignored, uncommitted).** PR body for the controller | T4 |

---

### Task 1: One law, four instances — the redaction quartet (rows 115, 117, 136, 188)

**Files:**
- Modify: `src/autoposter/api/logs.py` (imports ~:15-25; `emit` at :67-80)
- Modify: `src/autoposter/collections/service.py:392-395`
- Test: `tests/test_api_logs.py` (after the `LogBuffer` section, ~:99)
- Test: `tests/test_scheduler_collections_job.py` (after `BreaksOnSecondLibrary`, ~:146, and after the :209 test)
- Commit (Step 1 only): `docs/superpowers/plans/2026-08-31-hardening-sweep-1.md` (this plan)

**Interfaces:**
- Produces: `api/logs.py::_scrub(text: str) -> str` (module-private; nothing else imports it), and `LibraryOutcome.error` now carrying a class name (`"RuntimeError"`), not a message. Task 4's roadmap closes cite these.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Cut the branch and commit this plan**

```bash
git fetch origin
# Facts C7: base on origin/main once PR #102 is merged; on feat/location-names
# (86f55d4, = #102's content) if the gate is slow. Check, then cut:
gh pr view 102 --json state --jq .state
# If MERGED:   git checkout -b fix/hardening-sweep-1 origin/main
# Else:        git checkout -b fix/hardening-sweep-1 feat/location-names
#              (second-to-merge rebases -- the standing drill)
grep -q '^| 205 ' docs/superpowers/specs/2026-08-22-full-parity-roadmap.md && echo CONTENT-PROBE-OK
git add docs/superpowers/plans/2026-08-31-hardening-sweep-1.md
git commit --no-gpg-sign -m "docs(plan): hardening sweep 1 -- redaction, refusal caching, the flake, ParseError"
```

Expected: `CONTENT-PROBE-OK`. If the probe fails, STOP — the base predates #102's content and every line number in this plan is wrong.

- [ ] **Step 2: Verify row 115 is already delivered (no code change — evidence for its close)**

Row 115's body carries "**answered 8a:** delivered". Verify that holds at THIS tree; the wrap closes the row citing this step. The mechanism, read before running anything:

- `src/autoposter/collections/service.py:43-52` — `CollectionsPassFailed`, raised by the *callers*;
- `service.py:72-74` — `LibraryOutcome.ok` false on `error` OR `failed_definitions`; `:98-100` — `ReconcileResult.failed`;
- `src/autoposter/scheduler/jobs.py:108-116` — the job raises `CollectionsPassFailed(result.detail)` when `result.failed`, so `last_status` goes `failed`;
- `src/autoposter/collections/__main__.py:87-91` — the CLI exits 1 on `result.failed`.

Run the existing row-115 tests:

```bash
docker compose -p phard1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_scheduler_collections_job.py::test_a_failure_reconciling_one_library_does_not_prevent_the_other 'tests/test_builder_knobs.py::test_the_scheduled_job_fails_when_a_definition_failed' 'tests/test_builder_knobs.py::test_the_scheduled_run_row_records_the_failure' -q 2>&1 | tee /app/.superpowers/run-p-hard1-t1-row115.log"
```

Expected: `3 passed`. If any fails, STOP — row 115 has regressed and the plan's premise is wrong; report to the controller instead of improvising.

- [ ] **Step 3: Write the two failing LogBuffer tests (row 117)**

In `tests/test_api_logs.py`, after `test_buffer_includes_the_traceback_when_a_record_carries_one` (~:99), add:

```python
def test_credential_query_params_are_scrubbed_from_the_served_message():
    """Roadmap row 117: Fanart's api_key rides the query string, httpx embeds
    the full URL in an HTTPStatusError's message, and ladder.py:81 (and the
    candidates fan-out) log provider failures with exc_info -- the traceback's
    last line landed in this buffer and was served by /api/logs and the
    stream. The scrub is here, at the one point the served surface's text is
    assembled: stdout keeps the full traceback under the repo's host-only
    rule."""
    buffer = LogBuffer()
    try:
        raise ValueError(
            "Client error '401 Unauthorized' for url "
            "'https://webservice.fanart.tv/v3/movies/603?api_key=SECRETKEY'"
        )
    except ValueError:
        import sys

        buffer.emit(
            logging.LogRecord(
                name="autoposter.providers.ladder", level=logging.WARNING,
                pathname=__file__, lineno=1, msg="provider Fanart failed, continuing",
                args=(), exc_info=sys.exc_info(),
            )
        )
    (entry,) = buffer.lines()
    assert "SECRETKEY" not in entry["message"]
    assert "api_key=REDACTED" in entry["message"]
    # The traceback itself survives -- only the credential goes.
    assert "ValueError" in entry["message"]
    assert "provider Fanart failed" in entry["message"]


def test_a_token_param_in_a_plain_message_is_scrubbed_too():
    """The same law for a message that was never an exception: a URL with
    X-Plex-Token pasted into an ordinary log line must not be served intact."""
    buffer = LogBuffer()
    buffer.emit(record(
        "GET http://plex.internal:32400/library/all?X-Plex-Token=PLEXSECRET failed"
    ))
    (entry,) = buffer.lines()
    assert "PLEXSECRET" not in entry["message"]
    assert "X-Plex-Token=REDACTED" in entry["message"]
```

- [ ] **Step 4: Run them to verify they fail**

```bash
docker compose -p phard1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_api_logs.py -q -k scrub 2>&1 | tee /app/.superpowers/run-p-hard1-t1-red1.log"
```

Expected: `2 failed` — `AssertionError` on `"SECRETKEY" not in entry["message"]` and on `"PLEXSECRET" not in ...`.

- [ ] **Step 5: Implement the scrub in `api/logs.py`**

Add `import re` to the imports. Below the module's existing constants (after `HEARTBEAT_SECONDS`-area, before the `LogBuffer` class), add:

```python
# Roadmap row 117: Fanart's api_key rides the query string, httpx embeds full
# URLs in HTTPStatusError messages, and ladder.py:81 plus api/candidates.py's
# two fan-out sites log provider failures with exc_info -- so a credential
# could reach this buffer inside a traceback's last line and be served by
# /api/logs and the stream. Scrubbed HERE, where the served surface's text is
# assembled, rather than at the logging sites: stdout (the pod log) keeps the
# full traceback under the repo's host-only rule, and a future exc_info site
# is covered without knowing about this module. Matches any query-param name
# ending in api_key/apikey/token (X-Plex-Token, plex_token, ...); the name
# survives, the value never does.
_CREDENTIAL_PARAM = re.compile(r"(?i)([-\w]*(?:api_?key|token))=[^&\s'\"]+")


def _scrub(text: str) -> str:
    return _CREDENTIAL_PARAM.sub(r"\1=REDACTED", text)
```

In `emit` (:76), change the one line:

```python
                # format() appends the traceback when the record carries one.
                "message": _scrub(self.format(record)),
```

- [ ] **Step 6: Run the log tests to verify green**

```bash
docker compose -p phard1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_api_logs.py -q 2>&1 | tee /app/.superpowers/run-p-hard1-t1-green1.log"
```

Expected: all pass (the file's existing tests included — none of their fixture messages contain a credential param, so nothing else changes).

- [ ] **Step 7: Commit row 117**

```bash
git add src/autoposter/api/logs.py tests/test_api_logs.py
git commit --no-gpg-sign -m "fix(logs): credential query params never reach the served log surface (row 117)"
```

- [ ] **Step 8: Write the two failing rollback-boundary tests (rows 136/188)**

In `tests/test_scheduler_collections_job.py`, after `BreaksOnSecondLibrary` (~:146), add the fake:

```python
class BreaksWithATokenisedUrl:
    """The failure shape rows 136/188 exist for: a plexapi transport error
    whose message carries the request URL -- the operator's base URL and, in
    some shapes, a token. Every configured library fails this way."""

    class _Library:
        def section(self, name):
            raise RuntimeError(
                "GET http://plex.internal:32400/library/sections/9/all"
                "?X-Plex-Token=SECRETTOKEN returned 500"
            )

    def __init__(self):
        self.library = self._Library()
```

After `test_a_failure_reconciling_one_library_does_not_prevent_the_other` (~:225), add:

```python
async def test_a_library_failure_is_recorded_class_name_only(session):
    """Rows 136/188: service.py stored a bare str(error) on
    LibraryOutcome.error, and that string flows through ReconcileResult.detail
    -> CollectionsPassFailed -> scheduled_runs.last_detail (served by
    /api/snapshots) and into notifications. Class-name-only at the rollback
    boundary, the same rule the engine and the preview's _library_failure
    already apply; the full message and traceback stay in the log."""
    from autoposter.collections.service import reconcile_libraries

    config = _config(["Movies"])
    async with httpx.AsyncClient() as http:
        result = await reconcile_libraries(session, BreaksWithATokenisedUrl(), config, http)

    (outcome,) = result.libraries
    assert outcome.ok is False
    assert outcome.error == "RuntimeError"
    for surface in (outcome.summary, result.summary, result.detail):
        assert "SECRETTOKEN" not in surface
        assert "X-Plex-Token" not in surface


async def test_the_pass_failure_detail_never_carries_a_url(session):
    """Row 188's operator-facing surface: str(CollectionsPassFailed) is what
    scheduler/core.py:169/181 writes to scheduled_runs.last_detail (served by
    /api/snapshots, snapshots.py:123) and what the notification carries
    (core.py:195). One test at this boundary covers all three consumers --
    they all read this one string."""
    config = _config(["Movies"])
    async with httpx.AsyncClient() as http:
        job = make_collections_job(
            ConfigHolder(config), lambda: BreaksWithATokenisedUrl(), http
        )
        with pytest.raises(CollectionsPassFailed) as failure:
            await job.run(session)

    detail = str(failure.value)
    assert "RuntimeError" in detail
    assert "failed" in detail.lower()
    assert "SECRETTOKEN" not in detail
    assert "X-Plex-Token" not in detail
```

- [ ] **Step 9: Run them to verify they fail**

```bash
docker compose -p phard1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_scheduler_collections_job.py -q -k 'class_name_only or never_carries_a_url' 2>&1 | tee /app/.superpowers/run-p-hard1-t1-red2.log"
```

Expected: `2 failed` — `SECRETTOKEN` IS in the surfaces today (`error=str(error)`).

- [ ] **Step 10: Implement the rollback-boundary fix**

In `src/autoposter/collections/service.py`, replace the handler at :392-395:

```python
        except Exception as error:
            await session.rollback()
            logger.exception("failed reconciling %r", name)
            # Class-name-only (rows 136/188): a plexapi or provider failure's
            # message commonly carries the URL it failed on -- the operator's
            # base URL, in some shapes a token -- and this string flows through
            # ReconcileResult.detail -> CollectionsPassFailed ->
            # scheduled_runs.last_detail (served by /api/snapshots) and into
            # notifications. The full message and traceback are in the
            # logger.exception line above, where the host-only rule applies --
            # the same treatment the preview endpoint's _library_failure and
            # the engine's builder-exception rule already give this exact
            # failure shape.
            result.libraries.append(
                LibraryOutcome(library=name, error=type(error).__name__)
            )
```

- [ ] **Step 11: Run the touched files to verify green**

```bash
docker compose -p phard1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_scheduler_collections_job.py tests/test_collection_main.py tests/test_builder_knobs.py -q 2>&1 | tee /app/.superpowers/run-p-hard1-t1-green2.log"
docker compose -p phard1 down
```

Expected: all pass. (`test_collection_main.py` constructs `LibraryOutcome(error="simulated")` directly and `test_builder_knobs.py` asserts definition titles, not error messages — verified at plan time; if either fails, read the failure before touching it: the fix must not weaken what those tests pin.)

- [ ] **Step 12: Commit rows 136/188**

```bash
git add src/autoposter/collections/service.py tests/test_scheduler_collections_job.py
git commit --no-gpg-sign -m "fix(collections): library failures recorded class-name-only at the rollback boundary (rows 136/188)"
```

---

### Task 2: Refusals must not linger or escape — MDBList's cached limit body and the ParseError tuple (rows 147, 205)

**Files:**
- Modify: `src/autoposter/providers/fetch.py` (whole function shown below)
- Modify: `src/autoposter/facts/mdblist.py` (~:152 new helper; `:212` and `:269` call sites; `:253-260` docstring paragraph)
- Modify: `src/autoposter/collections/enrichment.py` (imports :15-18; comment+tuple :60-73)
- Modify: `src/autoposter/collections/builders/plex_search.py` (imports :49-54; comment+tuple :458-472)
- Test: `tests/test_mdblist.py`, `tests/test_collection_enrichment.py`, `tests/test_builder_plex_search.py`

**Interfaces:**
- Produces: `fetch_json(..., cacheable: Callable[[object], bool] | None = None)` — keyword-only, default None = cache every 2xx exactly as today. Every existing caller passes keywords, so no other client changes.
- Consumes: nothing from Task 1.

**Scope note (ambiguity resolved):** C3 says "never cache a limit/error body"; row 147's own text says "do not cache a limit-error body at all". This task skips the cache ONLY for bodies whose `error` is in `_LIMIT_ERRORS` — a non-limit error body (a deleted/private list) is a stable answer whose caching *saves* budget, and it is the exact analogue of the 404 that `fetch_json` deliberately caches. The tmdb_budget law being mirrored is "a REFUSAL is never a cached answer", and MDBList's refusal is the limit body. Recorded in the row's close (Task 4).

- [ ] **Step 1: Write the three failing MDBList tests**

In `tests/test_mdblist.py`, add to the top imports (after :16):

```python
from sqlalchemy import select

from autoposter.db.models import ProviderCache as ProviderCacheRow
```

After `test_a_cached_404_still_returns_none` (~:177), add:

```python
async def test_a_limit_body_is_never_cached_for_a_list(session):
    """Roadmap row 147, tmdb_budget's law mirrored: MDBList answers a spent
    daily budget with 200 and {"error": "API Limit Reached!"}, which fetch_json
    stored like any other success -- so a later pass, including one after
    midnight when the allowance had already rolled over, was served the cached
    refusal until the entry aged out, up to a full TTL late. A refusal must
    never become a cached answer (tests/test_tmdb_budget.py::
    test_a_refusal_body_is_never_cached is the shipped precedent)."""
    from autoposter.facts.mdblist import MDBListLimitReached

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": "API Limit Reached!"})
        )
    ) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        with pytest.raises(MDBListLimitReached):
            await client.list_items("user/some-list")

    rows = (await session.execute(select(ProviderCacheRow))).scalars().all()
    assert rows == [], "a refusal must never become a cached answer"


async def test_a_limit_body_is_never_cached_for_a_content_rating(session):
    """The same law on the other endpoint the client owns."""
    from autoposter.facts.mdblist import MDBListLimitReached

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"error": "API Limit Reached!"})
        )
    ) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        with pytest.raises(MDBListLimitReached):
            await client.content_rating(tmdb_id=1, is_movie=True)

    rows = (await session.execute(select(ProviderCacheRow))).scalars().all()
    assert rows == [], "a refusal must never become a cached answer"


async def test_the_first_ask_after_rollover_reaches_the_transport_again(session):
    """Row 147's consequence, refused end to end: the allowance rolls over at
    midnight, and the very next ask must reach MDBList -- not a cached
    refusal. This is the recovery-latency half of the row: with no cached
    limit body, recovery is the first request after rollover, not TTL-late."""
    from autoposter.facts.mdblist import MDBListLimitReached

    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, json={"error": "API Limit Reached!"})
        return httpx.Response(200, json=[
            {"mediatype": "movie", "id": 1, "title": "Dune"},
        ])

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = MDBListClient("KEY", http, cache=cache, cache_ttl_seconds=3600)
        with pytest.raises(MDBListLimitReached):
            await client.list_items("user/some-list")
        items = await client.list_items("user/some-list")

    assert len(calls) == 2, "the post-rollover ask must reach the transport"
    assert [(kind, entry["title"]) for kind, entry in items] == [("movie", "Dune")]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker compose -p phard2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_mdblist.py -q -k 'never_cached or rollover' 2>&1 | tee /app/.superpowers/run-p-hard1-t2-red1.log"
```

Expected: `3 failed` — the cache rows exist (`rows == []` fails) and the second `list_items` is served the cached refusal (`MDBListLimitReached` raised instead of returning items / `len(calls) == 2` fails).

- [ ] **Step 3: Implement — `fetch_json`'s `cacheable` predicate and MDBList's use of it**

Replace `src/autoposter/providers/fetch.py`'s function (the whole file below the docstring/imports):

```python
async def fetch_json(
    *,
    method: str,
    url: str,
    params: Mapping[str, object] | None,
    request: Callable[[], Awaitable[httpx.Response]],
    cache: ProviderCache | None,
    ttl_seconds: int,
    cacheable: Callable[[object], bool] | None = None,
) -> dict | None:
    """Run one JSON request, optionally through the cache.

    Returns the decoded payload, or None for a 404 — the caller's confirmed
    "nothing there" case, which every client already turns into an empty
    candidate list. The cache key is derived only from ``method``/``url``/
    ``params``, never from ``request``'s headers, which is where every client's
    credentials actually live; a negative (404) result is cached with the same
    TTL as a positive one, distinct from "not cached" (see ProviderCache.get).

    ``cacheable`` lets a caller veto the cache WRITE for a decoded 2xx payload
    it recognises as a refusal in disguise (roadmap row 147: MDBList answers a
    spent daily budget with 200 and an error body, and caching that served the
    refusal as an answer for the whole TTL). None means every 2xx is cached,
    exactly as before; the 404 write is not consulted — a confirmed "nothing
    there" is an answer, not a refusal.
    """
    key = None
    if cache is not None:
        key = build_cache_key(method, url, params)
        cached = await cache.get(key)
        if cached is not None:
            return cached["payload"] if cached["found"] else None

    response = await request()
    if response.status_code == 404:
        if key is not None:
            await cache.set(key, {"found": False, "payload": None}, ttl_seconds)
        return None
    response.raise_for_status()
    payload = response.json()
    if key is not None and (cacheable is None or cacheable(payload)):
        await cache.set(key, {"found": True, "payload": payload}, ttl_seconds)
    return payload
```

In `src/autoposter/facts/mdblist.py`, after `_raise_for_error` (~:153), add:

```python
def _not_a_limit_body(payload: object) -> bool:
    """The cache-write veto (roadmap row 147): the daily-budget refusal must
    never become a cached answer -- tmdb_budget's never-cache-a-refusal law,
    applied to a provider whose refusal arrives as a 200. A NON-limit error
    body (a deleted or private list) stays cacheable on purpose: it is a
    stable answer whose caching saves budget, the analogue of the 404
    fetch_json already caches."""
    return not (
        isinstance(payload, dict) and payload.get("error") in _LIMIT_ERRORS
    )
```

Add `cacheable=_not_a_limit_body,` to BOTH `fetch_json` calls — in `content_rating` (after `ttl_seconds=self._cache_ttl_seconds,` at :220) and in `list_items` (same position, :277).

In `list_items`'s docstring, replace the stale final paragraph (:253-260, "One consequence of going through ``fetch_json`` worth knowing: …rolls over.") with:

```
        The 200-with-an-error-body that means "budget spent" is never written
        to the provider cache (``_not_a_limit_body``, roadmap row 147): within
        a pass the builder's memo already stops the spending, and across
        passes the first ask after midnight's rollover reaches MDBList afresh
        instead of being served a cached refusal until the entry aged out.
```

- [ ] **Step 4: Run the mdblist and cache tests to verify green**

```bash
docker compose -p phard2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_mdblist.py tests/test_provider_cache.py tests/test_builder_mdblist.py tests/test_tmdb_budget.py -q 2>&1 | tee /app/.superpowers/run-p-hard1-t2-green1.log"
```

Expected: all pass (the predicate defaults to None everywhere else; `tmdb_budget`'s own law-test is in the run to prove the seam change didn't disturb it). Note: `src/autoposter/facts/tmdb_budget.py:29-36`'s docstring recounts row 147 in the PAST tense ("was written into provider_cache") — that stays true as history and is deliberately not edited.

- [ ] **Step 5: Commit row 147**

```bash
git add src/autoposter/providers/fetch.py src/autoposter/facts/mdblist.py tests/test_mdblist.py
git commit --no-gpg-sign -m "fix(mdblist): the daily-limit body is never a cached answer (row 147)"
```

- [ ] **Step 6: Write the two failing ParseError tests (row 205)**

In `tests/test_collection_enrichment.py`: add `from xml.etree import ElementTree` to the imports, extend the parametrize at :64 to `[BadRequest, requests.ConnectionError, ElementTree.ParseError]`, and extend the comment above it (:60-63):

```python
# All three arms of ``enrichment.py``'s narrowed catch: ``BadRequest`` is D2's
# measured refusal shape (a ``PlexApiException`` subclass); ``ConnectionError``
# is the dropped connection that comes off plexapi's bare ``requests`` call
# unwrapped; ``ParseError`` is the 200-with-an-HTML-body a reverse proxy or
# captive portal answers, which escapes plexapi's unguarded cleaned retry
# (utils.py:836-844) as a ``SyntaxError`` subclass in NEITHER hierarchy
# (roadmap row 205).
```

In `tests/test_builder_plex_search.py`: add `from xml.etree import ElementTree` to the imports, and after `test_a_transport_failure_in_the_lookup_is_wrapped_class_name_only` (~:327), add:

```python
async def test_a_parse_error_in_the_lookup_is_wrapped_class_name_only():
    """Roadmap row 205. plexapi's ``utils.parseXMLString`` (utils.py:836-844,
    v4.18.2) catches the first ``ParseError`` only to retry ``fromstring`` on
    a cleaned string, and that retry is UNGUARDED -- a body that is not XML at
    all (a reverse proxy answering 200 with an HTML error page) fails the
    retry the same way, and the second ``ParseError`` -- a ``SyntaxError``
    subclass, in neither ``PlexApiException`` nor ``RequestException`` --
    escaped both catch tuples: the operator got a raw traceback instead of
    the named refusal, and the pass re-attempted a read that would fail again
    instead of memoising the failure."""
    section = FakeSection(
        raise_on={"genre": ElementTree.ParseError("syntax error: line 1, column 0")}
    )
    ctx = context(section, config={"all": {"genre": "Horror"}})
    with pytest.raises(PlexSearchUnavailable) as error:
        await PlexSearchBuilder().build(ctx)
    message = str(error.value)
    assert "ParseError" in message
    assert "syntax error" not in message
```

- [ ] **Step 7: Run them to verify they fail**

```bash
docker compose -p phard2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_enrichment.py -q -k ParseError 2>&1 | tee -a /app/.superpowers/run-p-hard1-t2-red2.log; pytest tests/test_builder_plex_search.py -q -k parse_error 2>&1 | tee -a /app/.superpowers/run-p-hard1-t2-red2.log"
```

Expected: both fail with a raw `xml.etree.ElementTree.ParseError` propagating instead of `EnrichmentUnavailable` / `PlexSearchUnavailable` — the row's escape, demonstrated.

- [ ] **Step 8: Implement — both sites TOGETHER (C5: one commit, tuple + stale comment)**

`src/autoposter/collections/enrichment.py` — add to the imports (:15-18, stdlib group):

```python
from xml.etree import ElementTree
```

Extend the comment above the catch — after "hence the second class." (:72) append:

```python
    # A body that is not XML at all -- a reverse proxy or captive portal
    # answering 200 with an HTML error page -- escapes BOTH: plexapi's
    # ``utils.parseXMLString`` (utils.py:836-844, v4.18.2) catches the first
    # ``ParseError`` only to retry ``fromstring`` on a cleaned string, that
    # retry is unguarded, and ``ParseError`` is a ``SyntaxError`` subclass in
    # neither hierarchy above -- hence the third (roadmap row 205; the class
    # list is deliberately coupled to plex_search.py's, which documents the
    # same mechanism).
```

Change the tuple at :73:

```python
    except (PlexApiException, requests.RequestException, ElementTree.ParseError) as error:
```

`src/autoposter/collections/builders/plex_search.py` — add to the imports (with :49's stdlib group):

```python
from xml.etree import ElementTree
```

Replace the comment block at :458-471 and the tuple at :472 (the false clause was "a malformed response or an auth failure is ``PlexApiException`` itself" — true for auth, FALSE for malformed; malformed is exactly the case that escapes):

```python
        # A second, DIFFERENT kind of Plex-originating failure -- not "this
        # filter does not exist" but "Plex did not answer at all", or answered
        # something that is not XML. ``query()`` hands the request straight to
        # a bare ``requests`` call (``self._session.get``), so a dropped
        # connection or a timeout is a ``requests.RequestException``, not a
        # ``PlexApiException``, and reaches here unwrapped; an auth failure is
        # ``PlexApiException`` itself, above ``NotFound``/``BadRequest`` in
        # its hierarchy. A body that is not XML at all -- a reverse proxy or
        # captive portal answering 200 with an HTML error page -- is NEITHER:
        # plexapi's ``utils.parseXMLString`` (utils.py:836-844, v4.18.2)
        # catches the first ``ParseError`` only to retry ``fromstring`` on a
        # cleaned string, that retry is unguarded, and the second
        # ``ParseError`` is a ``SyntaxError`` subclass outside both classes
        # (roadmap row 205). Caught here rather than folded into the clause
        # above because "Plex has no such filter" would be a FALSE claim about
        # the library for any of the three -- class-name-only, like the other
        # Plex exception this module wraps, because each can carry a tokenised
        # URL in its own message (Task 4 review, Fix-round Carry 1: the
        # narrowing above, alone, silently dropped this wrap and let that
        # message reach the engine's logger intact).
        except (PlexApiException, requests.RequestException, ElementTree.ParseError) as error:
```

- [ ] **Step 9: Run both test files to verify green**

```bash
docker compose -p phard2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_collection_enrichment.py tests/test_builder_plex_search.py -q 2>&1 | tee /app/.superpowers/run-p-hard1-t2-green2.log"
docker compose -p phard2 down
```

Expected: all pass.

- [ ] **Step 10: Commit row 205 — both sites, one commit**

```bash
git add src/autoposter/collections/enrichment.py src/autoposter/collections/builders/plex_search.py tests/test_collection_enrichment.py tests/test_builder_plex_search.py
git commit --no-gpg-sign -m "fix(collections): ParseError joins both stream-read catch tuples, stale comment corrected (row 205)"
```

---

### Task 3: The pipeline-upsert flake — reproduce first, then decide (row 195)

**Files:**
- Instrument (uncommitted, reverted or superseded): `tests/test_pipeline.py:319-349`
- Modify only per the decision tree: `tests/test_pipeline.py:349` (branch B) OR `src/autoposter/render/pipeline.py:311` (branch C)
- Artifacts: `.superpowers/run-p-hard1-loop195.log`, `.superpowers/p-hard1-195-diag.txt` (both gitignored)

**Interfaces:**
- Produces: a recorded outcome — REPRODUCED (with the diag numbers and which branch's fix landed) or NOT-REPRODUCED-IN-200 (the re-file evidence). Task 4's row-195 edit consumes exactly this.
- Consumes: nothing from Tasks 1–2.

**The mechanism under test** (row 195, verified at this tree): both writes take `updated_at` from `func.now()` = `transaction_timestamp()` — the insert via `MediaItem.updated_at`'s `server_default` (`src/autoposter/db/models.py:66-68`) and the conflict path via `_upsert_media_item`'s explicit `set_={..., "updated_at": func.now()}` (`src/autoposter/render/pipeline.py:311`, which exists because `onupdate=` never fires on `INSERT … ON CONFLICT DO UPDATE`). The 2026-08-29 sighting saw the timestamps go BACKWARDS ~0.92s across two commits separated by a 1.1s sleep, full-suite-only. The hypothesis is transaction-start semantics on pooled connections; it has never been reproduced on demand. **Never blind-stabilize:** no edit to the assertion or the column until the loop has spoken.

- [ ] **Step 1: Apply the diagnostic instrumentation (uncommitted)**

In `tests/test_pipeline.py`, replace the body of `test_second_upsert_of_the_same_rating_key_refreshes_updated_at` (:319-349) with the version below — the assertions are UNCHANGED; the only additions are the three-clock reads inside each write's own transaction (before its commit, so `transaction_timestamp()` is the one `func.now()` served) and the diag append:

```python
async def test_second_upsert_of_the_same_rating_key_refreshes_updated_at(session_factory):
    # on_conflict_do_update is an INSERT statement, so SQLAlchemy's onupdate=
    # hook (which only fires for genuine UPDATEs) never runs on its own; the
    # set_ mapping must refresh updated_at explicitly on every conflict.
    # Compare the two timestamps against each other, not against the host
    # clock: this machine's Postgres clock lags the host clock by several
    # seconds (see project constraints).
    import os

    from sqlalchemy import text as sqltext

    from autoposter.render.pipeline import _upsert_media_item

    def _diag(label, row):
        with open("/app/.superpowers/p-hard1-195-diag.txt", "a", encoding="utf-8") as fh:
            fh.write("%s %s txid=%s xact=%s clock=%s\n" % (
                os.environ.get("PYTEST_XDIST_WORKER", "master"), label, *row,
            ))

    async with session_factory() as s:
        await _upsert_media_item(s, item(title="Dune: Part Two"))
        _diag("write1", (await s.execute(sqltext(
            "select txid_current(), transaction_timestamp(), clock_timestamp()"
        ))).one())
        await s.commit()

    async with session_factory() as s:
        first = (
            await s.execute(select(MediaItem).where(MediaItem.rating_key == "1"))
        ).scalar_one()

    await asyncio.sleep(1.1)

    async with session_factory() as s:
        await _upsert_media_item(s, item(title="Dune: Part Two (Extended)"))
        _diag("write2", (await s.execute(sqltext(
            "select txid_current(), transaction_timestamp(), clock_timestamp()"
        ))).one())
        await s.commit()

    async with session_factory() as s:
        second = (
            await s.execute(select(MediaItem).where(MediaItem.rating_key == "1"))
        ).scalar_one()

    assert second.created_at == first.created_at
    assert second.updated_at > first.updated_at
```

Do NOT commit this. Verify the patch runs: one plain pass of the file first —

```bash
docker compose -p phard3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest tests/test_pipeline.py -q -n auto --dist loadgroup 2>&1 | tee /app/.superpowers/run-p-hard1-loop195-smoke.log"
```

Expected: all pass, and `.superpowers/p-hard1-195-diag.txt` gains two lines (write1/write2 with monotonic values).

- [ ] **Step 2: Run the loop — 200 iterations of the file under the suite's own concurrency**

The suite's own concurrency is `-n auto --dist loadgroup` (ci.yml:342; conftest gives each xdist worker its own database). Detached, no `--rm` — this run takes on the order of 1.5–3 hours; start it, then poll, never foreground-wait the whole thing:

```bash
docker compose -p phard3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name phard3-loop test sh -c '
    for i in $(seq 1 200); do
      echo "=== iteration $i ===" >> /app/.superpowers/run-p-hard1-loop195.log
      pytest tests/test_pipeline.py -q -n auto --dist loadgroup \
        >> /app/.superpowers/run-p-hard1-loop195.log 2>&1 \
        || { echo "FAILED at iteration $i" >> /app/.superpowers/run-p-hard1-loop195.log; break; }
    done
    echo "LOOP DONE" >> /app/.superpowers/run-p-hard1-loop195.log'
# Poll (repeat as needed; do not sleep-spin):
docker ps --filter name=phard3-loop --format '{{.Status}}'
tail -5 .superpowers/run-p-hard1-loop195.log
```

When the container exits:

```bash
grep -c '^=== iteration' .superpowers/run-p-hard1-loop195.log
grep -n 'FAILED at iteration' .superpowers/run-p-hard1-loop195.log || echo NO-REPRODUCTION
docker compose -p phard3 down
```

Also check the adjacency note the facts file demands: `grep -n 'test_worker' .superpowers/run-p-hard1-loop195.log || echo NO-193-SIGHTING` — row 193 (same observational class, different file) gains a sighting note ONLY if its file appears; its code is not touched either way.

- [ ] **Step 3: Decide from the evidence — the decision tree**

Read the failing iteration's block in the loop log and the matching `write1`/`write2` lines in `.superpowers/p-hard1-195-diag.txt`.

**Branch A — no failure in 200 iterations.** Revert the instrumentation (`git checkout -- tests/test_pipeline.py`), make NO code change, and record for Task 4: row 195 **re-files** with the evidence — 200 iterations of `tests/test_pipeline.py` under `-n auto --dist loadgroup` (the sighting's own concurrency), zero failures, log at `.superpowers/run-p-hard1-loop195.log`, diag clocks monotonic throughout. That is C4's threshold, honoured exactly.

**Branch B — reproduced, and the diag shows `clock` (clock_timestamp) ALSO went backwards between write1 and write2.** The database's wall clock stepped backwards (the containerised VM's time sync) — no column semantics can survive that, and the assertion's premise (wall-clock monotonicity across a 1.1s sleep) is what's defective. Fix the ASSERTION: revert the instrumentation, then change :349 (with its comment) to

```python
    # The contract is that the conflict path RE-STAMPS updated_at (onupdate=
    # never fires on INSERT ... ON CONFLICT DO UPDATE); equality is the one
    # shape the regression produces. Strict `>` additionally assumed the DB
    # wall clock is monotonic across the sleep, and the hardening-sweep loop
    # reproduced a backwards step (row 195's close has the numbers) -- an
    # environment fact, not an upsert defect.
    assert second.updated_at != first.updated_at
```

RED-first is satisfied by the reproduced loop failure itself (the red run is on file in the loop log). Re-run the file 5x green, then commit:

```bash
git add tests/test_pipeline.py
git commit --no-gpg-sign -m "test(pipeline): upsert refresh asserted as re-stamp, not wall-clock order (row 195)"
```

**Branch C — reproduced, `clock` monotonic but `xact` (transaction_timestamp) inverted.** The row's pooled-transaction hypothesis is CONFIRMED: `func.now()` served a transaction start older than the previous write's. The ROOT is the conflict path's clock choice. Fix `src/autoposter/render/pipeline.py:311`:

```python
    stmt = stmt.on_conflict_do_update(
        # clock_timestamp(), not now(): now() is transaction_timestamp() -- the
        # moment the enclosing transaction BEGAN -- and the hardening-sweep
        # loop caught it serving a conflict re-stamp OLDER than the row's
        # existing value (row 195). The re-stamp must record the write moment.
        index_elements=["rating_key"], set_={**mutable, "updated_at": func.clock_timestamp()},
    )
```

Revert the test instrumentation (`git checkout -- tests/test_pipeline.py`; the `>` assertion now holds by construction), re-run the loop for 50 iterations green as verification, then commit:

```bash
git add src/autoposter/render/pipeline.py
git commit --no-gpg-sign -m "fix(pipeline): upsert conflict re-stamp uses clock_timestamp, not transaction start (row 195)"
```

**Any other shape** (reproduced but the diag contradicts both B and C, or the failure is in a different test): STOP. Use `superpowers:systematic-debugging`, and report the evidence to the controller before ANY edit — this plan does not pre-authorise a fix it could not predict.

- [ ] **Step 4: Confirm the working tree holds only the intended change**

```bash
git status --short
```

Expected: clean (branch A) or exactly the one committed file (branches B/C). The instrumentation must NOT survive in the tree.

---

### Task 4: Wrap — rows closed/re-filed, PR body, full suite (NO push, NO PR)

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (rows 115 :217, 117 :219, 136 :238, 147 :249, 188 :284, 195 :291, 205 :301)
- Create (gitignored, uncommitted): `.superpowers/sdd/p-hard1-pr-body.md`

**Interfaces:**
- Consumes: Task 1's verification evidence (row 115) and fix lines; Task 2's scope note (147) and commit; Task 3's recorded outcome (195).

- [ ] **Step 1: Close the six rows in the roadmap (and settle 195 per Task 3)**

For each row: append `(CLOSED — hardening sweep 1)` to the TITLE cell (the format row 196/182/199 use), and append a close note to the body cell. Exact notes:

- **Row 115:** `**closed hardening-sweep-1:** verified delivered at this tree, no code change — the 8a mechanism holds end to end (service.py:43-52/72-100, jobs.py:108-116 re-raises CollectionsPassFailed, __main__.py:87-91 exits 1) and its three tests pass (run-p-hard1-t1-row115.log). Closed as paperwork; the detail it surfaces is redacted by row 136's fix.`
- **Row 117:** `**closed hardening-sweep-1:** scrubbed at the served surface, not the sites — LogBuffer.emit (api/logs.py) redacts any query-param name ending api_key/apikey/token from the formatted text (traceback included) before it enters the buffer /api/logs and the stream serve; stdout keeps the full traceback under the host-only rule, and ladder.py:81 plus candidates.py's two fan-out sites are covered without edits, future exc_info sites with them.`
- **Row 136:** `**closed hardening-sweep-1:** same fix as row 188 — the two rows had drifted onto one line (this row's service.py:274 is :395 at close time). LibraryOutcome.error is class-name-only now; the message and traceback stay in the logger.exception line above it.`
- **Row 188:** `**closed hardening-sweep-1:** one redaction at the rollback boundary, as specified — service.py records type(error).__name__ into LibraryOutcome.error, so summary/detail/CollectionsPassFailed/last_detail/notifications all inherit it; tested at the service surface and the job surface (test_scheduler_collections_job.py).`
- **Row 147:** `**closed hardening-sweep-1:** the first option, mirroring tmdb_budget's never-cache-a-refusal law — fetch_json gained a cacheable veto and MDBList declines the cache write for a limit body (both endpoints; tests mirror test_a_refusal_body_is_never_cached). Recovery latency, reasoned: within a pass the builder memo still stops the spending; across passes the first ask after rollover reaches MDBList (pinned by test_the_first_ask_after_rollover_reaches_the_transport_again) — recovery is one request after midnight, not TTL-late. A NON-limit error body still caches, deliberately: it is a stable answer (the 404 analogue), and not caching it would spend budget re-asking about dead lists.`
- **Row 195:** per Task 3's recorded outcome. Branch A: NO close — append to the body: `**re-filed hardening-sweep-1:** not reproduced in 200 loop iterations of the file under -n auto --dist loadgroup (the sighting's concurrency; run-p-hard1-loop195.log), three-clock instrumentation monotonic throughout. Still one full-suite sighting, now with a bounded-loop no-show on record; the tempting >= relaxation stays unmade.` Branches B/C: close with the diag numbers, which branch fired, and the committed fix.
- **Row 205:** `**closed hardening-sweep-1:** ElementTree.ParseError added to the tuple at both sites in one commit — enrichment.py and plex_search.py — with the stale malformed-response clause corrected in the same edit and a red test per site (the escape demonstrated raw before the fix).`

- [ ] **Step 2: Commit the roadmap**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): hardening sweep 1 -- rows 115/117/136/188/147/205 closed, 195 settled on loop evidence"
```

(Adjust the message's last clause to `closed` or `re-filed` per Task 3.)

- [ ] **Step 3: Write the PR body (gitignored, NOT committed)**

Write `.superpowers/sdd/p-hard1-pr-body.md`: title `fix: hardening sweep 1 — redaction, refusal caching, the upsert flake, ParseError`; sections — **What** (one paragraph per task: the buffer scrub + the rollback boundary; the cacheable veto + the two-site tuple; the loop and its outcome; the paperwork), **Why** (the seven rows' one theme: failures and secrets surfaced honestly, never leaking or lingering; rows 136/188 found to be one line; 115 already delivered, closed on verification), **Evidence** (the red logs run-p-hard1-t*-red*.log, the loop log and diag file paths, the full-suite number from Step 4), **Behaviour changes an operator can see** (library failure detail now names the exception class, not the message — the message stays in the pod log; /api/logs never shows credential query params; MDBList definitions recover the morning after a spent budget instead of up to a TTL late; a proxy's HTML error page becomes the named refusal, memoised for the pass), **Not done here** (the OUT families listed in Global Constraint 1, by name). Plain prose, no AI attribution anywhere in it.

- [ ] **Step 4: Full suite — stated, then measured**

**The computation, stated before the run:** this branch's base measures 4028 (4027 at #102's reviewed HEAD + the polish trio's one covering test, per the ledger). This plan adds 9 tests: +4 in Task 1 (two LogBuffer scrubs, two rollback-boundary surfaces), +5 in Task 2 (three MDBList cache-law tests, one new enrichment parametrize case, one plex_search ParseError test); Task 3 adds none on any branch (branch B rewrites an assertion, branch C touches src only). **Expected: 4037 passed, 0 failed.** The wrap MEASURES; the measured number wins, and any shortfall is investigated with `superpowers:systematic-debugging` before any completion claim — never explained away.

```bash
docker compose -p phard4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name phard4-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-p-hard1-full.log"
docker wait phard4-full
tail -30 .superpowers/run-p-hard1-full.log
docker compose -p phard4 down
```

Expected: `4037 passed` (or 4028+N' where N' is re-derived above if any task's test count changed during execution — state the revised arithmetic in the report if so), `0 failed`.

- [ ] **Step 5: Verify the branch is clean and parked**

```bash
git status --short   # expected: empty (pr-body, logs and diag are gitignored)
git log --oneline origin/main..HEAD 2>/dev/null || git log --oneline -8
```

Report the commit list and the measured suite number. **Do NOT push. Do NOT open a PR.** The controller runs the whole-branch review and does both.

---

## Self-Review (performed at write time)

1. **Spec coverage:** C1's seven rows each map to a task (115/117/136/188 → T1; 147/205 → T2; 195 → T3; closes → T4). C2's law and the convergence analysis are in Global Constraint 2 + the dedicated section; verdict: no four-site abstraction. C3 → T2 Steps 1–5 with the recovery-latency reasoning in the row close. C4 → T3's loop command, 200-iteration threshold, decision tree. C5 → T2 Steps 6–10, one commit, comment corrected. C6 → T4. C7 → 4 tasks, `fix/hardening-sweep-1`, phard1–4, p-hard1- artifacts, RED-first at every fix.
2. **Placeholder scan:** every code step shows the code; every run step names the command and the expected outcome; T3's branches each carry their exact edit or their exact no-edit.
3. **Type/name consistency:** `_scrub`/`_CREDENTIAL_PARAM` (T1) are logs.py-private; `cacheable`/`_not_a_limit_body` (T2) match between fetch.py's signature and mdblist.py's call sites; `BreaksWithATokenisedUrl` is defined in the same file that uses it; `ProviderCacheRow` aliases `db.models.ProviderCache` exactly as `test_tmdb_budget.py` does.
4. **Drift check:** every cited line number re-verified against this tree at write time — notably 136's `:274` → `:395` (recorded in the row's close), 205's comment `:463-464` → `:462-464`, and 195's `models.py:62-64` → `:66-68`.
