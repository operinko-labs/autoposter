# Search Tails 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close roadmap rows 151, 153 (open half), 171 (open half) and 178 — the four rows `p-tails2-facts.md`/`p-tails2-recon.md` (2026-08-31) adjudicated in-phase and probe-free — while re-fencing row 148 with its text corrected, on branch `feat/search-tails-2`.

**Architecture:** T1 gives `awards.py`'s two YAML fetchers a persistent, TTL'd cache by generalizing the existing `fetch_json` seam with a defaulted decoder, then adds a live-data category-coverage drift guard that logs (never raises) beside it. T2 gives `filters.py` the `current_year`/`current_year-N` value grammar via a resolved-at-match-time sentinel mirroring `_Today`, then gives search-side `.regex` a real vocabulary-expansion implementation under the same spelling, sequenced because both touch `filters.py`'s value/operator layers. T3 corrects row 148's text, adds an oracle config for `current_year`, and runs the full suite.

**Tech Stack:** Python 3, pytest (`asyncio_mode = "auto"`), httpx `MockTransport`, SQLAlchemy (`ProviderCache`'s `provider_cache` table), pydantic.

## Global Constraints

- Every grammar/vocabulary addition proven RED first (C2).
- The drift guard is proven RED via fixture mutation, never against live IMDb (C2); `tests/conftest.py:258-274`'s `no_outbound_network` fixture blocks any transport but `MockTransport`/`ASGITransport`.
- Row 151's decoder change disturbs no existing `fetch_json` caller: every caller's tests stay green unchanged (C2). The eight existing call sites are `facts/mdblist.py:224,279`, `facts/tmdb_facts.py:239`, `providers/tmdb_lists.py:203`, `providers/tvdb.py:163`, `providers/tmdb.py:124`, `providers/fanart.py:97`, `providers/tracearr.py:187` — none pass a `decode` keyword today, so a defaulted parameter leaves every one byte-for-byte unchanged.
- Totals are stated then measured: baseline is CONFIRMED 4231 passed / 0 failed at `9dac9a6` (14:56 wall time, cold-image artifact per the recon's own note — not a regression signal); frontend 362 stands from sweep-6's own measurement. State the new total in T3, then run it.
- Row 153's guard content is class/id-style only, WARNING to the pod log (the trusted sink) — no new served surface, one line per drifted category. The category and award-key strings logged are this codebase's OWN hardcoded vocabulary (the `BEST_PICTURE`-style tuples and `ImdbAwardParams.award`), never text read live off IMDb, so logging them verbatim carries no injection/PII surface — flagged explicitly in Task 1 below since the facts ask for this to be adjudicated rather than assumed.
- Row 178 ships the SAME `.regex` spelling for search as `filters:` uses (Kometa-parity naming doctrine) — no new operator name.
- Container: this branch's own artifacts and PR block ptl21–ptl23 (project tracking IDs; no code implication).
- Sonnet throughout; opus on block only (process note for whoever executes this plan, not a code constraint).
- Branch `feat/search-tails-2` from `chore/sweep-6`'s tip `9dac9a6`. Row 148 is OUT — re-fenced, not touched by any task below except T3's text correction.

---

### Task 1: Branch cut, and row 151 — a persistent TTL cache for the award dataset fetch

**Files:**
- Create branch: `feat/search-tails-2` from `9dac9a6`
- Modify: `src/autoposter/providers/fetch.py` (add a defaulted `decode` parameter to `fetch_json`)
- Modify: `src/autoposter/collections/awards.py` (`fetch_event_validation`, `fetch_event` route through `fetch_json`)
- Modify: `src/autoposter/collections/builders/imdb_award.py` (`_event` threads `ctx.cache` through)
- Modify: `src/autoposter/collections/builders/imdb_award.py` (row 153: `uncovered_categories` call + WARNING log in `ImdbAwardBuilder.build`)
- Modify: `src/autoposter/collections/awards.py` (row 153: new `uncovered_categories` function)
- Test: `tests/test_provider_cache.py` (the decoder)
- Test: `tests/test_collection_awards.py` (the cache wiring, and the drift guard)

**Interfaces:**
- Consumes: `fetch_json(*, method, url, params, request, cache, ttl_seconds, cacheable=None)` (existing, `src/autoposter/providers/fetch.py:17`); `ProviderCache` (`src/autoposter/providers/cache.py:35`); `BuilderContext.cache: ProviderCache | None` (`src/autoposter/collections/builders/base.py:166`); `_memoised(ctx, key, fetch)` (`src/autoposter/collections/builders/imdb_award.py:551`).
- Produces: `fetch_json(..., decode: Callable[[httpx.Response], object] = <json>)` — a new keyword-only parameter, defaulted, so every existing caller is untouched. `fetch_event_validation(http, *, cache=None, ttl_seconds=86400)` and `fetch_event(http, event_id=EVENT_ID, *, cache=None, ttl_seconds=86400)` — both gain keyword-only `cache`/`ttl_seconds`, positional callers unaffected. `uncovered_categories(event, categories, award_filter=None) -> tuple[str, ...]` — a pure function T1 Step 13 adds; Task 3 does not consume it further, but its name and signature are load-bearing for anyone reading this task out of order.

- [ ] **Step 1: Cut the branch and confirm its content**

```bash
git branch feat/search-tails-2 9dac9a6
git checkout feat/search-tails-2
git merge-base --is-ancestor 9dac9a6 HEAD && echo "content probe: 9dac9a6 is HEAD's own tip"
git log --oneline -1
```

Expected: the branch is created at `9dac9a6`, checked out, and the log line reads `9dac9a6 docs(roadmap): row 218 closes across all six sites plus the logo.py upgrade` — the same commit this plan's recon read. If the log line differs, STOP: the tip has moved and the recon's line-number citations below may no longer be accurate; re-verify against the new tip before continuing.

- [ ] **Step 2: Write the failing test for `fetch_json`'s decoder**

Add to `tests/test_provider_cache.py`, after `test_fetch_json_without_a_cache_makes_a_request_every_time` (around line 178):

```python
async def test_fetch_json_accepts_a_custom_decoder_for_a_non_json_body(cache):
    """Row 151: the seam generalizes to a decoder rather than growing a
    parallel ``fetch_yaml``. A YAML body would raise inside ``response.json()``
    if the default decoder ran, so this proves the parameter actually swaps it
    out rather than merely being accepted and ignored."""
    calls = []

    async def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text="a: 1\nb: 2\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs = dict(
        method="GET", url="https://api.example.com/x", params=None,
        request=lambda: client.get("https://api.example.com/x"),
        cache=cache, ttl_seconds=3600,
        decode=lambda response: {"a": 1, "b": 2},
    )

    first = await fetch_json(**kwargs)
    second = await fetch_json(**kwargs)

    assert first == {"a": 1, "b": 2}
    assert second == {"a": 1, "b": 2}
    assert len(calls) == 1
```

- [ ] **Step 3: Run it to verify it fails**

Run: `docker compose run --rm test pytest tests/test_provider_cache.py::test_fetch_json_accepts_a_custom_decoder_for_a_non_json_body -v`
Expected: FAIL with `TypeError: fetch_json() got an unexpected keyword argument 'decode'`

- [ ] **Step 4: Add the `decode` parameter to `fetch_json`**

In `src/autoposter/providers/fetch.py`, replace the whole function:

```python
def _decode_json(response: httpx.Response) -> object:
    return response.json()


async def fetch_json(
    *,
    method: str,
    url: str,
    params: Mapping[str, object] | None,
    request: Callable[[], Awaitable[httpx.Response]],
    cache: ProviderCache | None,
    ttl_seconds: int,
    cacheable: Callable[[object], bool] | None = None,
    decode: Callable[[httpx.Response], object] = _decode_json,
) -> dict | None:
    """Run one JSON-shaped request, optionally through the cache.

    Returns the decoded payload, or None for a 404 -- the caller's confirmed
    "nothing there" case, which every client already turns into an empty
    candidate list. The cache key is derived only from ``method``/``url``/
    ``params``, never from ``request``'s headers, which is where every client's
    credentials actually live; a negative (404) result is cached with the same
    TTL as a positive one, distinct from "not cached" (see ProviderCache.get).

    ``cacheable`` lets a caller veto the cache WRITE for a decoded 2xx payload
    it recognises as a refusal in disguise (roadmap row 147: MDBList answers a
    spent daily budget with 200 and an error body, and caching that served the
    refusal as an answer for the whole TTL). None means every 2xx is cached,
    exactly as before; the 404 write is not consulted -- a confirmed "nothing
    there" is an answer, not a refusal.

    ``decode`` turns the response into the payload the cache stores and the
    caller gets back. Defaulted to plain ``response.json()``, which is every
    caller's behaviour before this parameter existed -- TMDB, TVDB and Fanart
    never pass it and stay byte-for-byte unchanged. ``awards.py`` passes a
    YAML decoder instead (roadmap row 151): the "JSON-shaped" name this module
    keeps is now about the response's shape at the wire (a body decodable to a
    plain dict), not about the wire format, which is exactly the gap the
    module docstring's own text names.
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
    payload = decode(response)
    if key is not None and (cacheable is None or cacheable(payload)):
        await cache.set(key, {"found": True, "payload": payload}, ttl_seconds)
    return payload
```

- [ ] **Step 5: Run it to verify it passes**

Run: `docker compose run --rm test pytest tests/test_provider_cache.py -v`
Expected: PASS, including every pre-existing `test_fetch_json_*` case (none pass `decode`, so they exercise the default).

- [ ] **Step 6: Commit**

```bash
git add src/autoposter/providers/fetch.py tests/test_provider_cache.py
git commit -m "feat(providers): fetch_json takes a defaulted decoder, for row 151"
```

- [ ] **Step 7: Write the failing test for `awards.py`'s cache wiring**

Add to `tests/test_collection_awards.py`, after `test_fetch_event_raises_on_an_http_error` (around line 127). Needs the `session_factory`/`ProviderCache` fixtures other suites use — add the imports at the top of the file alongside the existing ones:

```python
from autoposter.providers.cache import ProviderCache
```

```python
async def test_fetch_event_validation_is_cached_across_calls(session_factory):
    """Row 151: a second call within the TTL must not hit the transport
    again -- that is the whole complaint the row was filed for, sixteen
    ceremonies' worth of it per pass."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=VALIDATION_FIXTURE)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = ProviderCache(session_factory)

    first = await fetch_event_validation(http, cache=cache, ttl_seconds=3600)
    second = await fetch_event_validation(http, cache=cache, ttl_seconds=3600)

    assert first == second
    assert len(calls) == 1


async def test_fetch_event_is_cached_across_calls(session_factory):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=FIXTURE)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache = ProviderCache(session_factory)

    first = await fetch_event(http, cache=cache, ttl_seconds=3600)
    second = await fetch_event(http, cache=cache, ttl_seconds=3600)

    assert first == second
    assert len(calls) == 1


async def test_fetch_event_validation_without_a_cache_hits_the_transport_every_time():
    """The prior, uncached behaviour stays available -- a direct caller with
    no ProviderCache, exactly as ``BuilderContext.cache``'s own docstring
    says None means."""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=VALIDATION_FIXTURE)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await fetch_event_validation(http)
    await fetch_event_validation(http)

    assert len(calls) == 2
```

`session_factory` is the suite-wide fixture other DB-backed tests already use (`tests/test_provider_cache.py:15` builds `ProviderCache(session_factory)` from it); it is defined in `tests/conftest.py` and needs no import.

- [ ] **Step 8: Run it to verify it fails**

Run: `docker compose run --rm test pytest tests/test_collection_awards.py -k "cached_across_calls or without_a_cache" -v`
Expected: FAIL — `fetch_event_validation()`/`fetch_event()` raise `TypeError: ...got an unexpected keyword argument 'cache'`.

- [ ] **Step 9: Route `fetch_event_validation`/`fetch_event` through `fetch_json`**

In `src/autoposter/collections/awards.py`, add the imports and a module-level TTL constant near the top (after the existing `BASE_URL` line):

```python
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

EVENT_ID = "ev0000003"
BASE_URL = "https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master"

# The same class-default every provider client already carries --
# ``providers/fanart.py:81``, ``providers/tmdb.py:69``, ``providers/tvdb.py:108``,
# ``facts/mdblist.py:204`` -- all default ``cache_ttl_seconds: int = 24 * 3600``.
# Award category data changes roughly once a year per ceremony, so this is not
# a tight bound; it is the existing convention, reused rather than invented
# (roadmap row 151's TTL decision). ``BuilderContext`` carries no application
# config for a builder to read a live value from (only ``cache`` itself), so
# the literal is the correct level for this to live at, not a threaded-through
# setting.
_CACHE_TTL_SECONDS = 24 * 3600
```

Replace `fetch_event_validation` and `fetch_event`:

```python
async def fetch_event_validation(
    http: httpx.AsyncClient,
    *,
    cache: ProviderCache | None = None,
    ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> dict:
    """Every event id the community dataset covers, keyed by id.

    The same file Kometa reads first (``imdb.py:1034``) and for the same
    reason: it is the only way to know whether an event has data *before*
    asking for a file that may not exist. Ours is a smaller question than
    Kometa's -- it decides git-versus-scrape, we decide build-versus-refuse.

    Routed through ``providers.fetch.fetch_json`` since roadmap row 151:
    ``ProviderCache`` needed no extension to hold a YAML-decoded dict, only a
    decoder at the fetch site (``fetch_json``'s ``decode`` parameter). ``cache``
    is None for a direct caller -- unchanged behaviour, one request per call.
    """
    url = "%s/event_validation.yml" % BASE_URL
    validation = await fetch_json(
        method="GET", url=url, params=None,
        request=lambda: http.get(url),
        cache=cache, ttl_seconds=ttl_seconds,
        decode=lambda response: yaml.safe_load(response.text),
    )
    if not isinstance(validation, dict) or not validation:
        raise ValueError("the award event validation list returned an unexpected body")
    return validation


async def fetch_event(
    http: httpx.AsyncClient,
    event_id: str = EVENT_ID,
    *,
    cache: ProviderCache | None = None,
    ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> dict:
    """The whole event dataset. Raises rather than returning nothing.

    A 404 here is now a ``fetch_json`` "confirmed nothing there" (cached as a
    negative, same as every other provider's 404 convention) rather than an
    ``httpx.HTTPStatusError`` raised directly -- this function still raises
    either way, now via the same ``ValueError`` an unexpected-but-200 body
    already got, so the builder contract (``build`` raises, the engine
    contains) is unchanged. What is new: a genuine 404 on this URL is now
    memoised as "not found" for the TTL rather than re-requested every pass --
    correct for the same reason every other provider client already caches its
    404s, since this file's URL does not 404 in ordinary operation.
    """
    url = "%s/events/%s.yml" % (BASE_URL, event_id)
    event = await fetch_json(
        method="GET", url=url, params=None,
        request=lambda: http.get(url),
        cache=cache, ttl_seconds=ttl_seconds,
        decode=lambda response: yaml.safe_load(response.text),
    )
    if not isinstance(event, dict) or not event:
        raise ValueError("award event %r returned an unexpected body" % event_id)
    return event
```

- [ ] **Step 10: Run it to verify it passes**

Run: `docker compose run --rm test pytest tests/test_collection_awards.py -v`
Expected: PASS for the whole file, including every pre-existing test (`test_fetch_event_raises_on_an_http_error` still passes: a 404 now raises `ValueError` instead of `HTTPStatusError`, and the test asserts the generic `pytest.raises(Exception)`).

- [ ] **Step 11: Commit**

```bash
git add src/autoposter/collections/awards.py tests/test_collection_awards.py
git commit -m "feat(collections): award dataset fetches cache through ProviderCache (row 151)"
```

- [ ] **Step 12: Write the failing test for threading `ctx.cache` through `_event`**

Add to `tests/test_collection_awards.py`, after the tests from Step 7:

```python
async def test_the_oscars_builder_reuses_a_provider_cache_across_separate_passes(session_factory):
    """Row 151's actual complaint: an N-library deployment refetches the same
    398 KB file once per library, every pass. Two separate ``run_cache``
    dicts stand in for two libraries/passes sharing one process-lifetime
    ``ProviderCache`` -- the transport must still be hit once per URL."""
    requests = []
    async with _events_client(requests) as http:
        cache = ProviderCache(session_factory)
        first_pass = _ctx(http, {"award": "best_picture"}, run_cache={})
        second_pass = _ctx(http, {"award": "best_picture"}, run_cache={})
        first_pass = replace(first_pass, cache=cache)
        second_pass = replace(second_pass, cache=cache)

        await ImdbAwardBuilder().build(first_pass)
        await ImdbAwardBuilder().build(second_pass)

    assert requests.count(VALIDATION_URL) == 1
    assert requests.count(OSCARS_URL) == 1
```

- [ ] **Step 13: Run it to verify it fails**

Run: `docker compose run --rm test pytest tests/test_collection_awards.py::test_the_oscars_builder_reuses_a_provider_cache_across_separate_passes -v`
Expected: FAIL — `requests.count(VALIDATION_URL) == 2` (each pass's own `run_cache` re-fetches, since only `ctx.cache` would have prevented the second request and `_event` never reads it yet).

- [ ] **Step 14: Thread `ctx.cache` through `_event`**

In `src/autoposter/collections/builders/imdb_award.py`, replace `_event`:

```python
async def _event(ctx: BuilderContext, event: AwardEvent) -> dict:
    """This ceremony's dataset, fetched at most once per pass.

    The validation list is consulted first and memoised separately: it is one
    file for every ceremony, so a pass building two of them still asks for it
    once, and an event the dataset dropped is refused in words rather than
    surfacing as a 404 on the event file.

    ``ctx.cache`` (roadmap row 151) sits BENEATH the ``_memoised``/``run_cache``
    layer above, not instead of it: ``run_cache`` is per-pass in-process scratch
    and stops a second collection in the SAME pass from re-fetching; ``cache``
    is the persistent, TTL'd store and stops the NEXT pass (or the next library
    in an N-library deployment) from re-fetching. Both are needed, because they
    answer different questions.
    """
    validation = await _memoised(
        ctx, _VALIDATION, lambda: fetch_event_validation(ctx.http, cache=ctx.cache)
    )
    require_known_event(validation, event.event_id)
    return await _memoised(
        ctx,
        "imdb_award.event:%s" % event.event_id,
        lambda: fetch_event(ctx.http, event.event_id, cache=ctx.cache),
    )
```

- [ ] **Step 15: Run it to verify it passes**

Run: `docker compose run --rm test pytest tests/test_collection_awards.py -v`
Expected: PASS for the whole file.

- [ ] **Step 16: Commit**

```bash
git add src/autoposter/collections/builders/imdb_award.py tests/test_collection_awards.py
git commit -m "feat(collections): imdb_award threads ctx.cache through _event (row 151)"
```

- [ ] **Step 17: Write the failing test for `uncovered_categories`**

Add to `tests/test_collection_awards.py`, after `test_duplicates_are_removed_keeping_first_occurrence` (around line 212):

```python
# --------------------------------------------------------------------------
# Live drift detection over category vocabularies (roadmap row 153, the
# open half: a runtime guard riding data already fetched, not a new fetch).
# --------------------------------------------------------------------------

async def test_uncovered_categories_is_empty_when_every_category_appears():
    async with _client() as http:
        event = await fetch_event(http)
    assert uncovered_categories(event, BEST_PICTURE) == ()


async def test_uncovered_categories_names_the_ones_that_never_appear():
    async with _client() as http:
        event = await fetch_event(http)
    drifted = ("best picture", "a renamed category nobody transcribed")
    assert uncovered_categories(event, drifted) == (
        "a renamed category nobody transcribed",
    )


async def test_uncovered_categories_ignores_a_category_with_zero_winners_this_year():
    """Not covered != has a winner. A category present in the data with an
    empty winner list some year is ordinary; only the NAME never appearing
    anywhere is drift."""
    event = {"2026": {"oscar": {"best picture": {"nominee": ["ttX"], "winner": []}}}}
    assert uncovered_categories(event, ("best picture",)) == ()


async def test_uncovered_categories_respects_the_award_group_filter():
    """A category name that exists, but only under a group the award_filter
    excludes, still counts as uncovered for THIS award -- the group filter
    narrows what "appears" means, exactly as it narrows winners_for_categories."""
    event = {"2026": {"other_group": {"best picture": {"winner": ["ttX"]}}}}
    assert uncovered_categories(event, ("best picture",), award_filter=("oscar",)) == (
        "best picture",
    )


async def test_uncovered_categories_is_empty_for_a_ceremony_with_no_category_filter():
    """The four ceremonies with ``categories=None`` (Berlinale, Cannes,
    Sundance, the National Film Registry) have nothing named to have
    drifted."""
    event = {"2026": {"oscar": {"best picture": {"winner": ["ttX"]}}}}
    assert uncovered_categories(event, None) == ()
```

- [ ] **Step 18: Run it to verify it fails**

Run: `docker compose run --rm test pytest tests/test_collection_awards.py -k uncovered_categories -v`
Expected: FAIL — `ImportError: cannot import name 'uncovered_categories'`. (Add it to the import block at the top of the test file first, alongside `winners_for_categories`.)

- [ ] **Step 19: Add `uncovered_categories` to `awards.py`**

In `src/autoposter/collections/awards.py`, add after `winners_for_categories` (after its closing `return _dedupe(ids)`, before `winners_for_year`):

```python
def uncovered_categories(
    event: dict,
    categories: tuple[str, ...] | None,
    award_filter: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Which of ``categories`` never appears anywhere in ``event``, across
    every year and (if narrowed) every wanted award group.

    Empty when ``categories`` is ``None`` -- there is nothing named to have
    drifted (the four ceremonies with no ``category_filter``). A category
    present with zero WINNERS some year is not "uncovered": winning nothing in
    a given year is ordinary Oscars history. The category NAME never
    appearing at all -- upstream renamed it after this codebase's tuple was
    transcribed -- is the drift roadmap row 153 was filed for, and it is the
    only thing this function reports.

    Rides data the caller already fetched this pass (``_event``'s live
    result); it makes no fetch of its own and belongs beside
    ``winners_for_categories``, not inside it, because the caller decides what
    to do with drift (roadmap row 153: log it) and this function only detects
    it.
    """
    if not categories:
        return ()
    wanted = {c.lower() for c in categories}
    groups = _wanted_groups(award_filter)
    seen: set[str] = set()
    for year_data in _by_year(event).values():
        for award, group in year_data.items():
            if groups is not None and award.lower() not in groups:
                continue
            seen.update(category.lower() for category in group)
    return tuple(c for c in categories if c.lower() not in seen)
```

- [ ] **Step 20: Run it to verify it passes**

Run: `docker compose run --rm test pytest tests/test_collection_awards.py -k uncovered_categories -v`
Expected: PASS, 5 tests.

- [ ] **Step 21: Commit**

```bash
git add src/autoposter/collections/awards.py tests/test_collection_awards.py
git commit -m "feat(collections): uncovered_categories, the pure coverage check (row 153)"
```

- [ ] **Step 22: Write the failing test for the WARNING log**

Add to `tests/test_collection_awards.py`, after the tests from Step 17-19's block:

```python
async def test_a_drifted_category_logs_a_warning_and_still_builds(caplog):
    """The BINDING shape (p-tails2-facts.md C1): WARNING to the pod log, not
    a raise -- the collection still builds with whatever it DID match, so one
    renamed category does not take an otherwise-healthy ceremony's whole
    build down. Proven by mutating the registry's category tuple against the
    pinned fixture, never against live IMDb (conftest.py's no_outbound_network
    fixture forbids that transport)."""
    import logging

    event = EVENTS["oscars"]
    award = event.awards["best_picture"]
    drifted_award = award._replace(
        categories=award.categories + ("a category imdb quietly renamed",)
    )
    drifted_event = replace(event, awards={**event.awards, "best_picture": drifted_award})

    with caplog.at_level(logging.WARNING):
        async with _events_client() as http:
            from unittest.mock import patch

            with patch.dict(EVENTS, {"oscars": drifted_event}):
                result = await ImdbAwardBuilder().build(
                    _ctx(http, {"event": "oscars", "award": "best_picture"})
                )

    assert result.ids, "the real categories still resolved winners"
    assert "a category imdb quietly renamed" in caplog.text
    assert "oscars" in caplog.text.lower() or event.name in caplog.text
```

Add `EVENTS` and `replace` (from `dataclasses`) to the test file's imports if not already present — `EVENTS` is already imported (line 47); `replace` is already imported (line 24).

- [ ] **Step 23: Run it to verify it fails**

Run: `docker compose run --rm test pytest tests/test_collection_awards.py::test_a_drifted_category_logs_a_warning_and_still_builds -v`
Expected: FAIL — `assert "a category imdb quietly renamed" in caplog.text` fails, nothing logged.

- [ ] **Step 24: Add the WARNING call to `ImdbAwardBuilder.build`**

In `src/autoposter/collections/builders/imdb_award.py`, add `import logging` and a module logger near the top (after the `from typing import NamedTuple` line):

```python
import logging
import re
from dataclasses import dataclass, field
from typing import NamedTuple
```

```python
logger = logging.getLogger(__name__)
```

placed after the imports block, before `_OSCAR_SUMMARY`.

Then in `ImdbAwardBuilder.build` (around line 632-657), insert the guard between the `_event` fetch and building the result:

```python
    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbAwardParams.model_validate(ctx.config)
        event = EVENTS[params.event]
        award = event.awards[params.award]
        require_library_type(
            "the %s %r collection" % (event.name, params.award),
            ctx.library_type,
            event.library_types,
        )
        data = await _event(ctx, event)
        # Roadmap row 153: a category tuple is this codebase's OWN transcribed
        # vocabulary (BEST_PICTURE and its siblings), never text read live off
        # IMDb -- so logging it verbatim carries no injection surface, unlike
        # a fetched title or summary would. class/id-style content only, per
        # the row's binding shape: WARNING, not a raise, because one renamed
        # category should not take the whole ceremony's build down.
        for category in uncovered_categories(data, award.categories, award.award_filter):
            logger.warning(
                "%s %r: category %r matched nothing in the live dataset -- "
                "check for an upstream rename", event.name, params.award, category,
            )
        poster_key = _poster(event, award.poster_stem)
        return BuilderResult(
            ids=[
                ("imdb", imdb_id)
                for imdb_id in winners_for_categories(
                    data, award.categories, award.award_filter
                )
            ],
            summary=award.summary,
            poster_kind="award_static" if poster_key else None,
            poster_key=poster_key,
        )
```

Add `uncovered_categories` to the `from autoposter.collections.awards import (...)` block at the top of the file, alphabetically between `require_known_event` and `winners_for_categories`.

- [ ] **Step 25: Run it to verify it passes**

Run: `docker compose run --rm test pytest tests/test_collection_awards.py -v`
Expected: PASS for the whole file (this is also the point to run the full `test_every_awards_filters_resolve_winners_in_its_own_dataset` parametrization again -- 16 cases, none should now emit a warning, since every shipped category is covered by its own fixture).

- [ ] **Step 26: Commit**

```bash
git add src/autoposter/collections/builders/imdb_award.py tests/test_collection_awards.py
git commit -m "feat(collections): imdb_award logs a WARNING on live category drift (row 153)"
```

---

### Task 2: Row 171 — `current_year`/`current_year-N`, then row 178 — search `.regex` vocabulary expansion

Sequential within one task (both touch `filters.py`'s value/operator layers, per the recon's own collision note) — 171 first, then 178.

**Files:**
- Modify: `src/autoposter/collections/filters.py` (171: `_CurrentYear`, `_as_current_year`, `_parse_value`, `_matches_one`, the `year` row's note)
- Modify: `src/autoposter/collections/filters.py` (178: `SEARCH_OPERATORS_BY_TYPE`, `SEARCH_MODIFIERS`, `_split_key`'s removed refusal)
- Modify: `src/autoposter/collections/search_url.py` (178: `TagResolver` protocol, `_arguments`' regex branch)
- Modify: `src/autoposter/collections/builders/plex_search.py` (178: module docstring correction)
- Test: `tests/test_collection_filters.py`
- Test: `tests/test_builder_plex_search.py`
- Test: `tests/test_collection_search_url.py`

**Interfaces:**
- Consumes: `_Today`/`_TODAY` sentinel pattern (`filters.py:1439-1453`); `_matches_one(attribute, operator, have, want, now)` (`filters.py:2045`); `_parse_value(attribute, operator, value, field, *, searching)` (`filters.py:1600`); `LibraryTagResolver.choices(attribute) -> tuple[tuple[str, str], ...]` (`plex_search.py:396`, unchanged by this task -- it already satisfies the extended protocol structurally).
- Produces: `_CurrentYear` (frozen dataclass, one field `offset: int`) -- nothing outside `filters.py` needs to import it, since `_parse_value`/`_matches_one` are the only producer and consumer. `TagResolver.choices(attribute: str, /) -> tuple[tuple[str, str], ...]` -- the protocol method Task-external callers of `build_search_url` must now supply on their `resolve_tag` argument if they ever write a `.regex` search predicate (existing callers whose configs never do keep working with a `choices`-less fake, since it is only invoked for `operator == "regex"`).

- [ ] **Step 1: Write the failing tests for the `current_year` grammar**

Add to `tests/test_collection_filters.py`, after `test_a_run_date_is_accepted_and_read_as_that_days_midnight` (around line 1395):

```python
# --- the current_year value grammar (roadmap row 171) -----------------------


def test_current_year_bare_resolves_to_the_runs_year():
    group = parse_filters({"year": "current_year"})
    assert evaluate(group, _view("year", 2026), now=NOW) is True
    assert evaluate(group, _view("year", 2025), now=NOW) is False


def test_current_year_with_an_offset_subtracts():
    """Kometa's own transcription (tests/oracle/9b/kometa_build_filter.py:
    768-788): ``datetime.now().year - int(year_values[1])`` -- subtraction,
    confirmed against the vendored oracle rather than an external citation."""
    group = parse_filters({"year": "current_year-5"})
    assert evaluate(group, _view("year", 2021), now=NOW) is True
    assert evaluate(group, _view("year", 2026), now=NOW) is False


def test_current_year_resolves_against_the_run_moment_not_the_parse_moment():
    """The whole point of the sentinel, mirroring ``_Today``: the same parsed
    group answers differently against a different run moment."""
    group = parse_filters({"year": "current_year"})
    assert evaluate(group, _view("year", 2026), now=NOW) is True
    later = dt.datetime(2027, 1, 1)
    assert evaluate(group, _view("year", 2026), now=later) is False
    assert evaluate(group, _view("year", 2027), now=later) is True


def test_current_year_is_case_insensitive_like_today_is():
    """Consistency with the ONE other sentinel word this module already has
    (``_as_date``'s ``.casefold() == "today"``, filters.py:1550) -- a
    deliberate divergence from Kometa's own literal ``str(value).
    startswith("current_year")`` (case-sensitive), in the direction this
    module already chose for ``today``."""
    group = parse_filters({"year": "Current_Year"})
    assert evaluate(group, _view("year", 2026), now=NOW) is True


def test_current_year_gt_and_lt_use_the_resolved_year():
    group = parse_filters({"year.gt": "current_year-3"})
    assert evaluate(group, _view("year", 2024), now=NOW) is True  # 2024 > 2023
    assert evaluate(group, _view("year", 2023), now=NOW) is False


def test_current_year_refuses_a_non_digit_suffix():
    with pytest.raises(ValueError, match="whole number"):
        parse_filters({"year": "current_year-abc"})


def test_current_year_refuses_whitespace_around_the_dash():
    """A tighter grammar than Kometa's own tolerant one (which strips
    whitespace inside ``year_values[1]``) -- an explicit narrowing, not an
    oversight: this module's ``.strip().lower()`` normalises the OUTER
    string, as every other string-form value in this module does, and does
    not special-case internal whitespace the way no other grammar here does
    either."""
    with pytest.raises(ValueError, match="whole number"):
        parse_filters({"year": "current_year - 5"})


def test_current_year_zero_offset_is_the_same_as_bare():
    group_bare = parse_filters({"year": "current_year"})
    group_zero = parse_filters({"year": "current_year-0"})
    assert evaluate(group_bare, _view("year", 2026), now=NOW) is True
    assert evaluate(group_zero, _view("year", 2026), now=NOW) is True


def test_plain_year_numbers_still_parse_as_before():
    group = parse_filters({"year": 1990})
    assert evaluate(group, _view("year", 1990), now=NOW) is True
    assert evaluate(group, _view("year", 1991), now=NOW) is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm test pytest tests/test_collection_filters.py -k current_year -v`
Expected: every `current_year`-string case FAILs — today's `year` row falls straight to `_as_int`, which raises `ValueError: ... is not a whole number` for `"current_year"` (a non-digit string). `test_plain_year_numbers_still_parse_as_before` PASSes already (unaffected baseline).

- [ ] **Step 3: Add the `_CurrentYear` sentinel and its parser**

In `src/autoposter/collections/filters.py`, add near `_Today`/`_TODAY` (after `_TODAY = _Today()`, before the `RelativeWindow` dataclass, around line 1453):

```python
@dataclass(frozen=True)
class _CurrentYear:
    """Kometa's ``current_year``/``current_year-N``, resolved against the
    run's MOMENT rather than the parse's -- the same deferred-resolution
    shape ``_Today`` already has for dates. Kometa's own transcription
    (``tests/oracle/9b/kometa_build_filter.py:768-788``, vendored for the 9b
    oracle rather than cited from Kometa's source tree, which is not in this
    repo) computes ``datetime.now().year - int(offset)`` -- subtraction, so
    ``current_year-5`` means five years ago, never five years from now.
    """

    offset: int = 0


_CURRENT_YEAR = re.compile(r"^current_year(?:-(\d+))?$")


def _as_current_year(value: object, field: str) -> "_CurrentYear | None":
    """``current_year`` or ``current_year-N``, or None for any value that is
    not this spelling -- the caller falls through to ``_as_int`` for an
    ordinary year number.

    Case-insensitive, like ``today`` (``_as_date``, above) -- a deliberate
    divergence from Kometa's own case-sensitive ``str(value).
    startswith("current_year")``, in the direction this module already chose
    for its one other sentinel word.

    No whitespace tolerance around the dash (``current_year - 5`` refuses,
    where Kometa's own parser would accept it via a ``.strip()`` on the
    split-off suffix) -- a deliberate narrowing: this module normalises the
    whole written string once, as every other string-form value here does,
    and no other grammar in this file tolerates internal whitespace either.
    A refusal here falls through to ``_as_int``'s "not a whole number"
    message, which is accurate: what is left is not a plain int and not this
    sentinel's spelling either.
    """
    if not isinstance(value, str):
        return None
    match = _CURRENT_YEAR.match(value.strip().lower())
    if not match:
        return None
    suffix = match.group(1)
    return _CurrentYear(offset=int(suffix) if suffix else 0)
```

- [ ] **Step 4: Wire it into `_parse_value`**

In `_parse_value` (`filters.py:1600-1628`), change the `int` branch:

```python
    if attribute.type == "int":
        if attribute.name == "year":
            current = _as_current_year(value, field)
            if current is not None:
                return current
        return _as_int(value, field)
```

This is the one branch in `_parse_value` that dispatches on the attribute's NAME rather than its kind -- every other branch (`bool`, `tag`/`str`, `int`, `float`, `duration`, `date`) dispatches purely on `attribute.type`. Worth the one-line note the recon flagged: a second `"int"`-kind attribute added later does NOT get `current_year` parsing for free, because nothing else names it the way `year` is named here.

- [ ] **Step 5: Wire the resolution into `_matches_one`'s number branch**

In `_matches_one` (`filters.py:2104-2113`), the un-named final branch (after the `date` `if kind ==` block, handling `int`/`float`/`duration`):

```python
    number = _as_number(have, attribute.name)
    target = now.year - want.offset if isinstance(want, _CurrentYear) else want
    if operator == "eq":
        return number == target
    if operator == "gt":
        return number > target
    if operator == "gte":
        return number >= target
    if operator == "lt":
        return number < target
    return number <= target
```

Note for whoever reviews this: this branch lives in `_matches_one`, not in a function named `_matches` -- `_matches` is a different function, starting at `filters.py:2116`, that calls `_matches_one` once per written value and inverts the result for a negative operator. The recon's own citation ("`_matches`'s int branch (`filters.py:2104-2113`)") names the wrong function; this is the correction the facts file's own citation-rot lesson asks for.

- [ ] **Step 6: Update the `year` row's note**

In `FILTER_ATTRIBUTES` (`filters.py:628-637`), replace the `year` row's note:

```python
    FilterAttribute(
        "year", "int", _BOTH, "listing",
        "The listing attrib `year`. Kometa's special year words (`current_year` "
        "and `current_year-N`) are supported (roadmap row 171): a "
        "`_CurrentYear` sentinel, parsed at load and resolved against the "
        "run's own moment at match time -- the same deferred-resolution "
        "pattern `_Today` already has for dates. Subtraction, per Kometa's own "
        "transcription (tests/oracle/9b/kometa_build_filter.py:768-788): "
        "`current_year-5` means five years ago. "
        "Bare/`.not` missing-value routing follows Kometa's tag branch -- see "
        "_matches and roadmap row 159.",
        search_field="year", show_search_field="show.year",
        search_kinds=_BOTH, filterable=True,
    ),
```

- [ ] **Step 7: Run it to verify it passes**

Run: `docker compose run --rm test pytest tests/test_collection_filters.py -v`
Expected: PASS for the whole file.

- [ ] **Step 8: Commit**

```bash
git add src/autoposter/collections/filters.py tests/test_collection_filters.py
git commit -m "feat(collections): current_year/current_year-N value grammar for year (row 171)"
```

- [ ] **Step 9: Update the two now-stale regex-refusal tests, and add a refusal test for a type that still lacks it**

In `tests/test_collection_filters.py`, replace `test_a_search_refuses_regex_and_says_why` (around line 1650):

```python
def test_a_search_accepts_regex_on_a_tag_attribute():
    """Roadmap row 178: search-side ``.regex`` on a tag/str attribute is now
    a real, distinct mechanism (vocabulary expansion, proven in
    ``test_collection_search_url.py``) -- parsing accepts it rather than
    refusing it."""
    group = parse_filters({"genre.regex": "^Hor"}, searching=True)
    [predicate] = group.children
    assert predicate.attribute.name == "genre"
    assert predicate.operator == "regex"


def test_a_search_refuses_regex_on_a_type_that_never_had_it():
    """``.regex`` is a tag/str-only mechanism in Kometa too (validate_attribute
    checks the tag list, then the string list -- never an int/float/date/
    duration/bool one). ``year`` (int) still refuses, now through the
    generic operator-table message rather than the old blanket one."""
    with pytest.raises(ValueError) as error:
        parse_filters({"year.regex": "^20"}, searching=True)
    message = str(error.value)
    assert ".regex does not apply to 'year'" in message
```

In `tests/test_builder_plex_search.py`, replace `test_a_search_regex_refuses_at_load_pointing_at_filters` (around line 180):

```python
def test_a_search_accepts_studio_regex_at_load():
    """Row 178's own exemplar attribute. Parsing alone does not need a
    library -- the vocabulary expansion happens at BUILD time -- so this only
    proves load-time acceptance; test_collection_search_url.py proves the
    render."""
    params = PlexSearchParams.model_validate({"all": {"studio.regex": "pictures$"}})
    [predicate] = params.group.children
    assert predicate.operator == "regex"
```

- [ ] **Step 10: Run it to verify it fails**

Run: `docker compose run --rm test pytest tests/test_collection_filters.py::test_a_search_accepts_regex_on_a_tag_attribute tests/test_builder_plex_search.py::test_a_search_accepts_studio_regex_at_load -v`
Expected: FAIL — both raise `ValueError`/`ValidationError`: `.regex is not a plex_search modifier` (the still-present blanket refusal).

- [ ] **Step 11: Add `regex` to the search operator and modifier tables**

In `src/autoposter/collections/filters.py`, in `SEARCH_OPERATORS_BY_TYPE` (`filters.py:259-296`):

```python
SEARCH_OPERATORS_BY_TYPE: dict[str, tuple[str, ...]] = {
    # ``.regex`` (roadmap row 178): a client-side vocabulary expansion, not a
    # regex Plex ever sees -- Kometa's own ``validate_attribute`` tests it
    # against the TAG list first (builder.py:4301), then the STRING list
    # (builder.py:4326), never against int/float/date/duration/bool. The
    # render layer (``search_url._arguments``) is what makes this safe to
    # ship under the SAME spelling ``filters:`` uses for a different
    # mechanism -- see its docstring for the divergence, documented
    # prominently there rather than merely in this comment.
    "tag": ("eq", "not", "regex"),
    "str": ("contains", "not", "is", "isnot", "begins", "ends", "regex"),
    "int": ("eq", "not", "gt", "gte", "lt", "lte"),
    "float": ("gt", "gte", "lt", "lte", "rated"),
    "duration": ("gt", "gte", "lt", "lte"),
    "date": ("eq", "not", "before", "after"),
    "bool": ("eq",),
}
```

(Only the `"tag"` and `"str"` lines change; the rest of the dict, and the comments above `"int"`/`"float"`/`"duration"` that were already there, are untouched.)

In `SEARCH_MODIFIERS` (`filters.py:353-382`), add two entries after `("tag", "not"): "!"`  and `("str", "ends"): "%3E"` respectively:

```python
SEARCH_MODIFIERS: dict[tuple[str, str], str] = {
    ("tag", "eq"): "",
    ("tag", "not"): "!",
    # ``.regex`` renders as bare, positive key terms -- it expands to zero
    # modifier prefix regardless of type, because the expansion always
    # produces exact resolved KEYS (see search_url._arguments), never a
    # negatable server-side predicate. Kometa's own search regex has no
    # ``.not`` counterpart either.
    ("tag", "regex"): "",
    ("str", "contains"): "",
    ("str", "not"): "!",
    ("str", "is"): "%3D",
    ("str", "isnot"): "!%3D",
    ("str", "begins"): "%3C",
    ("str", "ends"): "%3E",
    ("str", "regex"): "",
    ...
```

(Leave everything from `("int", "eq")` onward unchanged.)

In `_split_key` (`filters.py:1687-1695`), DELETE the explicit regex refusal block entirely:

```python
    if searching and modifier == "regex":
        raise ValueError(
            f"{field}: .regex is not a plex_search modifier. Kometa's search "
            "regex does not reach Plex at all -- it expands the pattern against "
            "the library's own tag vocabulary first and sends the matching tags "
            "(builder.py:4301-4323) -- so one spelling would mean two "
            "mechanisms. A `filters:` block on the same definition supports "
            ".regex client-side"
        )
```

With this block gone, `.regex` on a tag/str attribute falls through to the operator-table check (`filters.py:1716-1781`, unchanged) and is now accepted (present in `attribute.search_operators`); `.regex` on any other type falls through to the SAME check and is refused there, with the generic "`.regex` does not apply to `'year'`, an int attribute in a plex_search block -- it takes eq, not, gt, gte, lt, lte" message -- more precise than the deleted block's one-size-fits-all "two mechanisms" text, since for those types there was never a second mechanism to warn about, only an operator Kometa never lets you write there at all.

- [ ] **Step 12: Run it to verify it passes**

Run: `docker compose run --rm test pytest tests/test_collection_filters.py tests/test_builder_plex_search.py -v`
Expected: PASS for both files.

- [ ] **Step 13: Commit**

```bash
git add src/autoposter/collections/filters.py tests/test_collection_filters.py tests/test_builder_plex_search.py
git commit -m "feat(collections): .regex is a valid search operator for tag/str attributes (row 178)"
```

- [ ] **Step 14: Write the failing test for the render**

Add to `tests/test_collection_search_url.py`, after `test_an_unresolvable_tag_names_the_value_and_the_attribute` (find its end, then append):

```python
# --- .regex vocabulary expansion (roadmap row 178) ---------------------------
#
# A separate resolver from ``resolve``/``CHOICES`` above: those existing 30+
# tests pass a bare function with only ``__call__``, and nothing here changes
# that -- ``choices`` is only ever invoked for a ``.regex`` predicate, which
# no pre-existing test writes.

VOCAB = {
    "genre": (("1138", "Horror"), ("9", "Drama"), ("55", "Sci-Fi Horror")),
    "studio": (("12", "A24"), ("34", "Studio Ghibli"), ("56", "Warner Bros.")),
}


class _RegexResolver:
    def __call__(self, attribute, value, /):
        return CHOICES.get((attribute, value), ())

    def choices(self, attribute, /):
        return VOCAB.get(attribute, ())


def regex_url(raw, *, libtype="movie", base="all", **kwargs):
    group = parse_filters(raw, field="params", searching=True, base=base)
    return build_search_url(
        group, libtype=libtype, resolve_tag=_RegexResolver(), **kwargs
    )


def test_a_regex_search_sends_every_matching_titles_key():
    assert regex_url({"genre.regex": "Horror"}) == (
        "?type=1&sort=titleSort&genre=1138&and=1&genre=55"
    )


def test_a_regex_search_is_case_sensitive_like_filters_regex_is():
    """SETTLED-BY-ORACLE for ``filters:``'s own ``.regex`` (``_as_regex``,
    filters.py:1349-1373) -- the search-side expansion reuses the same
    compiled pattern with no flags, so the two stay consistent with each
    other even though they are different mechanisms. A lower-case pattern
    against the title-cased vocabulary (``"Horror"``, ``"Studio Ghibli"``)
    matches nothing, which is the same "no keys resolved" refusal an
    unmatched pattern gets anywhere else in this file."""
    with pytest.raises(TagValueNotFound):
        regex_url({"genre.regex": "^horror$"})


def test_a_regex_search_on_studio_matches_against_the_title_not_the_key():
    assert regex_url({"studio.regex": "^Studio"}) == (
        "?type=1&sort=titleSort&studio=34"
    )


def test_an_unmatched_regex_search_pattern_names_the_pattern():
    with pytest.raises(TagValueNotFound) as error:
        regex_url({"genre.regex": "^Nothing Matches This$"})
    assert "Nothing Matches This" in str(error.value)
    assert "genre" in str(error.value)


def test_a_regex_search_with_no_vocabulary_for_the_attribute_names_it():
    with pytest.raises(TagValueNotFound):
        regex_url({"resolution.regex": "1080"})
```

- [ ] **Step 15: Run it to verify it fails**

Run: `docker compose run --rm test pytest tests/test_collection_search_url.py -k regex -v`
Expected: FAIL — `KeyError: ('tag', 'regex')` out of `SEARCH_MODIFIERS` lookup inside `_arguments` (the top-of-function `modifier = SEARCH_MODIFIERS[(row.type, operator)]` line runs before any type-specific branch, and no branch handles `operator == "regex"` yet).

- [ ] **Step 16: Add `choices` to `TagResolver` and the regex branch to `_arguments`**

In `src/autoposter/collections/search_url.py`, extend the protocol:

```python
class TagResolver(Protocol):
    """The library's own tag vocabulary, as a callable.

    Returns the Plex KEYS one written value resolves to -- empty when the
    library has no such value, and SEVERAL when the value expands (a language
    code covering every locale variant the library carries). Injected rather
    than imported so this module stays pure; Task 4's builder passes the
    Plex-backed, run-cached one and the tests pass a dict.

    ``choices`` (roadmap row 178) is the enumeration half, used only for a
    ``.regex`` search predicate: every ``(key, title)`` the library reports
    for an attribute, so the pattern can be tested against each TITLE (the
    spelling an operator would otherwise write) rather than the opaque key.
    A ``resolve_tag`` whose config never writes ``.regex`` in a search never
    needs to implement it -- ``_arguments`` calls it only from the branch
    below.
    """

    def __call__(self, attribute: str, value: str, /) -> tuple[str, ...]: ...
    def choices(self, attribute: str, /) -> tuple[tuple[str, str], ...]: ...
```

Then in `_arguments` (`search_url.py:235-336`), add a branch immediately after the `rated`/`bool`/`duration`/`date` branches and BEFORE the `tag` branch (i.e. right before `if row.type == "tag":`):

```python
    # Roadmap row 178: Kometa's search ``.regex`` is a client-side expansion
    # over the library's own tag vocabulary, not a regex Plex ever evaluates
    # (builder.py:4301-4323) -- a DIFFERENT mechanism from ``filters:``'s
    # ``.regex``, which matches the item's own value client-side after
    # resolution. Same spelling, two mechanisms, on purpose (the project's
    # Kometa-parity naming doctrine) -- the divergence is documented here,
    # at the one place that actually renders it, rather than only in a
    # refusal message an operator who writes it correctly never reads.
    #
    # Renders as plain resolved-key terms regardless of the row's ``str``/
    # ``tag`` classification for ``filters:`` purposes -- ``studio`` is
    # ``str`` there (substring match) but its search vocabulary is still
    # enumerable via ``listFilterChoices``, which is what Kometa's own
    # ``validate_attribute`` falls back to for exactly this row (plex.py:568).
    if operator == "regex":
        out = []
        for pattern in predicate.values:
            keys = tuple(
                key for key, title in resolve_tag.choices(row.name)
                if pattern.search(title)
            )
            if not keys:
                raise TagValueNotFound(
                    f"{predicate.field}: {pattern.pattern!r} matched none of "
                    f"the {row.name} values this library uses"
                )
            out.extend((modifier, quote(str(key))) for key in keys)
        return out

    if row.type == "tag":
```

- [ ] **Step 17: Run it to verify it passes**

Run: `docker compose run --rm test pytest tests/test_collection_search_url.py -v`
Expected: PASS for the whole file.

- [ ] **Step 18: Update the two operator-surface docstrings that state the old blanket refusal**

In `src/autoposter/collections/builders/plex_search.py`, the module docstring (`plex_search.py:242-247`, inside `_the_block_must_parse_as_a_search`'s method docstring):

```python
        """The whole vocabulary check, at LOAD.

        Unknown attribute, a modifier the type does not take in a search, an
        unparseable value, a `.and`, a bare `duration:` -- every one of them
        refuses here, naming the key, hours before the pass. A tag/str
        `.regex` (roadmap row 178) is now accepted here and expanded at BUILD
        time, against the library's own vocabulary -- see
        `search_url._arguments`'s regex branch for the divergence from
        `filters:`'s `.regex`, which is a different mechanism under the same
        spelling. What CANNOT be checked here is anything that needs the
        library: which libtype, and whether a tag value exists (or, for
        `.regex`, whether the pattern matches anything the library has).
        Those are build-time, and the module docstring says why.
        ...
```

(Only the sentence listing what refuses at load changes -- drop `.regex` from that list and add the two sentences pointing at the new build-time behaviour; the rest of the docstring, including the "CANNOT be checked here" paragraph, keeps its existing wording apart from the one added clause about a `.regex` pattern matching nothing.)

- [ ] **Step 19: Run the full filters/search/plex_search suite once more**

Run: `docker compose run --rm test pytest tests/test_collection_filters.py tests/test_collection_search_url.py tests/test_builder_plex_search.py tests/test_collection_search_oracle.py -v`
Expected: PASS across all four files.

- [ ] **Step 20: Commit**

```bash
git add src/autoposter/collections/search_url.py src/autoposter/collections/builders/plex_search.py tests/test_collection_search_url.py
git commit -m "feat(collections): search .regex expands against the library's tag vocabulary (row 178)"
```

---

### Task 3: Wrap — row 148's text correction, an oracle config for `current_year`, and the full suite

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:180` (row 148's text)
- Create: an oracle config fixture proving `current_year` (exact path chosen in Step 3 below, following the existing oracle config directory's naming)
- Test: whichever oracle test file already runs the existing numbered configs (Step 3 identifies it)

**Interfaces:**
- Consumes: everything Task 1 and Task 2 produced. No new interfaces are produced by this task.

- [ ] **Step 1: Correct row 148's text in the roadmap**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:180`, replace the row 148 cell that currently reads:

```
| 148 | `imdb_search` Kometa-complete constraint surface | `AdvancedTitleSearchConstraints` also carries keyword, credit, country, language, certificate, runtime, award and list-membership constraints beyond row 81's minimal tier; none were built, and the live walk that would answer most of it is already banked (`.superpowers/sdd/archive/p8c-task-3-report.md` §1, §6.6) | S–M — the walk is largely done; each is a few more fields | parity-only | 81 |
```

with:

```
| 148 | `imdb_search` Kometa-complete constraint surface | `AdvancedTitleSearchConstraints` also carries keyword, credit, country, language, certificate, runtime, award and list-membership constraints beyond row 81's minimal tier; none were built. **CORRECTED (search-tails-2 recon):** the "live walk is already banked" claim above does not hold — `p8c-task-3-report.md`'s 71 probes (§1.1–1.6) walked only row 81's four shipped constraint objects, its own §6 item 6 (there is no §6.6) says plainly the other eight families (keyword, credit, country, language, certificate, runtime, award, list-membership) were never probed, and IMDb refuses introspection on `api.graphql.imdb.com`, so each of the eight needs its own probe-by-error-message session before it can ship without silently degrading a collection to empty — structurally identical to what fenced 176/177 out of tails-1. Re-fenced, not closeable without live IMDb probing; if any slice ships it is the cheapest single constraint (e.g. `runtimeConstraint`), scoped and probed as its own task, not this row as a whole | **re-fenced** — was S–M, is unknown per sub-constraint and NOT S–M as a whole; each of eight families is its own probe-first sub-investigation | parity-only | 81 |
```

- [ ] **Step 2: Run a markdown sanity check**

Run: `git diff docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`
Expected: exactly one table row changed, table structure (pipe count, column count) intact — visually confirm the row still has the same number of `|`-delimited cells as its neighbours.

- [ ] **Step 3: Locate the existing oracle config directory and its runner**

Run:
```bash
git -C /d/Sites/autoposter ls-files "tests/oracle/9b/configs/*.yml" | sort | tail -5
grep -rn "16-decade-and-country" tests/test_collection_search_oracle.py
```

Expected: the tail of the sorted list shows the highest-numbered existing config (e.g. `16-decade-and-country.yml` or later, per the recon's own citation of that config for `decade`'s proof), and the grep shows the test function that iterates the directory (parametrized over every `.yml` file it contains, since that is how every prior phase's oracle configs got exercised without a new per-file test).

- [ ] **Step 4: Write the failing oracle config for `current_year`**

Create `tests/oracle/9b/configs/17-current-year.yml` (bump the leading number past whatever Step 3 found as the highest existing one; if the highest is not 16, use `<highest + 1>` instead of 17):

```yaml
filters:
  year: current_year
```

- [ ] **Step 5: Run it to verify it is collected and exercised**

Run: `docker compose run --rm test pytest tests/test_collection_search_oracle.py -k current_year -v`
Expected: the new config is picked up by the existing parametrization (its filename appears in the test id) and PASSes if `year: current_year` in this file's `filters:` block resolves the SAME membership as Kometa's own standalone `build_filter` would for `year: current_year` against the oracle's fixture library at the pinned `NOW`. If the oracle harness needs a companion Kometa-side fixture file (mirroring how the other numbered configs pair with one), follow the SAME pairing convention Step 3's `grep` output shows for `16-decade-and-country` before calling this step done.

- [ ] **Step 6: Commit**

```bash
git add tests/oracle/9b/configs/17-current-year.yml
git commit -m "test(collections): oracle config proving current_year against Kometa (row 171)"
```

- [ ] **Step 7: State the total, then run the full suite**

Stated total: 4231 (CONFIRMED baseline) + Task 1's 3 + 3 + 5 + 1 = 12 new tests + Task 2's 9 (current_year) + 2 (replaced, net +1 over the 2 removed... count net new, not gross) + 6 (regex render) new tests + Task 3's 1 new oracle config test = **4231 + roughly 30 new, net of the 2 tests this plan replaces rather than adds** (re-count exactly from `git diff --stat` before stating the final number in the PR body, per C2's "totals stated then measured" law — do not copy the estimate above into the PR without running the count).

Run:
```bash
docker compose run --rm test pytest 2>&1 | tee /tmp/search-tails-2-suite.txt
tail -5 /tmp/search-tails-2-suite.txt
```

Expected: `N passed, 0 failed` where `N` is the CONFIRMED baseline (4231) plus the exact count of new tests this plan's diff added, computed from `git diff --stat` against `9dac9a6`, not estimated. If any test fails, STOP and use `superpowers:systematic-debugging` before touching anything further — do not proceed to Step 8 with a red suite.

- [ ] **Step 8: Confirm frontend baseline is untouched**

This plan makes no frontend changes (no file under a frontend/UI directory is modified by Task 1 or Task 2), so the frontend suite is not re-run — the 362 baseline stands unchanged by inspection of the diff (`git diff --stat 9dac9a6 -- '*.ts' '*.tsx' '*.js' '*.jsx'` should be empty). Run that command to confirm.

- [ ] **Step 9: Re-check branch ancestry before writing the PR body**

Run:
```bash
git fetch origin main chore/sweep-6 2>&1 | tail -5
git merge-base --is-ancestor 9dac9a6 origin/chore/sweep-6 && echo "9dac9a6 still chore/sweep-6's tip or an ancestor of it"
git log --oneline main..feat/search-tails-2 | wc -l
```

Expected: confirms whether `chore/sweep-6` (and roadmap row 115, the "SECOND behind #115 if pending at cut" note in the facts) has moved since this branch was cut. If it has moved in a way that changes any of the file/line citations this plan relied on, note it in the PR body rather than silently rebasing — the facts file calls this out explicitly as something the wrap re-checks, not something it fixes unilaterally.

- [ ] **Step 10: Write the PR body and close the roadmap rows**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, mark rows 151, 153, 171 and 178 CLOSED following this document's own existing convention for a closed row (see row 152's `(CLOSED — sweep 4)` suffix and row 171's own prior `**Half-closed by phase 10a-1:**` prose style for the exact phrasing pattern already in use). Row 148 stays as Step 1 left it — re-fenced, not closed.

No push, no PR creation — per the standing pattern this project's other phase plans follow (C4's "no push/PR per the standing pattern"), this task ends at a clean, fully green local branch with the roadmap updated and every commit made; a human decides when to push and open the PR.

---

## Self-review notes (for whoever executes this plan)

- **148's re-fence**: Task 3 Step 1 corrects the text but does not touch code — row 148 is genuinely out of scope for this phase, per both the facts and this plan's own Global Constraints.
- **151's TTL**: hardcoded `24 * 3600` in `awards.py`, matching the class-default convention four other provider clients already carry (cited by file:line in Step 9's code comment) — NOT read from `config.providers.cache_ttl_seconds`, because `BuilderContext` (what `awards.py`'s callers actually receive) carries no application config, only `cache` itself. Threading the live config value through would be a materially larger change this row's own size discipline (S–M) does not call for.
- **153's log content**: flagged explicitly in Task 1 Step 24's code comment — the category/award-key strings logged are this codebase's own hardcoded vocabulary tuples, never text fetched live from IMDb, so the WARNING carries no untrusted-content risk despite naming category strings verbatim.
- **171's citation correction**: this plan cites `_matches_one` (not `_matches`) for the int-comparison branch, correcting the recon's own imprecise citation, and cites the vendored oracle transcription (`tests/oracle/9b/kometa_build_filter.py:768-788`) rather than Kometa's own `builder.py:4333-4348`, which is not in this repository and cannot be verified against directly.
- **171's case-sensitivity and whitespace choices**: flagged as adjudications beyond the facts in this plan's own step comments (Step 3 of Task 2) — case-insensitive by analogy to `_Today`, no whitespace tolerance around the offset dash by analogy to every other grammar in the module. Both are real design choices a reviewer could reasonably make differently; they are not dictated by the facts file and are called out as such at the point they are made.
- **178's render-layer design**: the regex-expansion renders as bare, positive key terms regardless of the attribute's `filters:`-side `tag`/`str` classification (flagged in Task 2 Step 11's `SEARCH_MODIFIERS` comment and Step 16's `_arguments` comment) — this is inferred from the row's own text ("sends the matching tags to Plex") and from `studio` (a `str`-classified row) being the row's own worked example, but is not spelled out as a render-layer mechanism in either the facts or the recon and is worth a reviewer's explicit sign-off.
