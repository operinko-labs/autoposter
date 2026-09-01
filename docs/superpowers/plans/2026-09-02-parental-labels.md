# Parental Labels (row 85) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** close roadmap row 85 (`mass_imdb_parental_labels`) by fetching IMDb's own parental-guide categories per in-scope item and writing them to Plex as labels, default-off behind its own apply flag.

**Architecture:** a new provider client (`IMDbParentalGuideClient`) does one cached `title(id:)` GraphQL POST per item and returns `(category id, category text, severity text)` tuples, lenient about drift because a missing guide is the common case, not a fault. The render pipeline fetches those tuples alongside the existing metadata gather, and `plex/writer.py` turns them into `addLabel` calls inside the same batched write genres already use — additive only, casefold-compared against Plex's canonicalised label case.

**Tech Stack:** Python 3.13, httpx (async), pytest + pytest-asyncio, SQLAlchemy async (for the provider cache), Pydantic (config schema).

## Global Constraints

- Every new config field carries a non-empty `Field(description=...)` that says WHAT it does, never WHEN it takes effect (`tests/test_config_descriptions.py` enforces this for every model, no exemptions).
- No AI attribution in commits or PR bodies; commit with `git commit --no-gpg-sign` (this repo's convention, per `docs/superpowers/plans/2026-08-20-phase2a-metadata-operations.md`).
- Plex stages are referenced by name, never by a bare string comparison that assumes casing Plex might not preserve — labels are always compared casefolded (`operations.ignore_labels`'s own docstring: "Plex canonicalises label case").
- `severity.text` is the contract everywhere in this feature; `severity.id` (IMDb's internal `mildVotes`-shaped vote-bucket name) is parsed and immediately discarded, never stored or compared.
- `MockTransport` only in tests — no test in this plan makes a live request to `api.graphql.imdb.com`.
- RED before GREEN on every behavioural step: write the failing test, run it, watch it fail for the right reason, then implement.
- The gated-feature entry-point law (this repo's standing test shape, see `tests/test_mass_ops_verbs.py`'s last section): gate-off is byte-identical to pre-row-85 behaviour, gate-on fires through the real seam (`render.pipeline.apply_metadata`), and a second pass over an already-labelled item is steady (no re-fetch on a cache hit, no label churn when the severity hasn't changed).
- No vote-floor / minimum-vote config. Row 85's roadmap cell and its Kometa-inventory source name no threshold at all (see the STOP-and-file note in Task 3); a category can carry `"Severe"` off a single vote (probe: `tt0000009`, `totalSeverityVotes: 1`) and this build trusts IMDb's consensus as-is. Document the caveat; do not invent a knob.
- No label removal / sync semantics. The row states none, so this mass-op only ever adds labels, never strips one IMDb's guide no longer supports.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/autoposter/providers/imdb_parental_guide.py` (create) | The GraphQL query, `IMDbParentalGuideClient` (cached fetch), and the lenient parser that turns a response into `(category id, category text, severity text)` tuples or `None`. |
| `tests/test_imdb_parental_guide.py` (create) | T1's tests: parsing against the probe's verbatim fixtures, the `categories: null` shape, a malformed-entry drop, cache-hit steady state. |
| `tests/fixtures/providers/imdb_parental_shawshank.json`, `imdb_parental_paddington.json`, `imdb_parental_null.json` (create) | Verbatim captures from `p-row85-probe.md`, cited by section in the test file. |
| `src/autoposter/config/schema.py` (modify, `OperationsConfig`) | Three new fields: `parental_labels_enabled`, `parental_labels_apply`, `parental_labels_include_none`. |
| `src/autoposter/plex/writer.py` (modify) | `_current_labels`, `parental_label_edits`, `_apply_label_edits`; `plan_edits`/`apply_facts` grow an optional `parental_categories` parameter. |
| `src/autoposter/render/pipeline.py` (modify) | `_fetch_parental_categories`; `apply_metadata`/`process_item` grow an optional `imdb_parental` parameter and call the fetch + gate the write. |
| `src/autoposter/app.py` (modify) | Build one `IMDbParentalGuideClient` at startup (like `TMDBFactsClient`/`_build_mdblist`) and thread it through the `_handle_intent` partial. |
| `tests/test_mass_ops_parental_labels.py` (create) | T2's tests: `parental_label_edits` unit behaviour, and the three-part gated-feature entry-point test through `apply_metadata`. |
| `config/autoposter.example.yaml` (modify) | Document the three new fields next to row 84/86/87's existing commented block. |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (modify) | Close row 85: what shipped, the low-N caveat, the STOP-and-file notes. |

---

## Task 1: the IMDb parental-guide client (fetch + parse + cache)

**Files:**
- Create: `src/autoposter/providers/imdb_parental_guide.py`
- Create: `tests/fixtures/providers/imdb_parental_shawshank.json`
- Create: `tests/fixtures/providers/imdb_parental_paddington.json`
- Create: `tests/fixtures/providers/imdb_parental_null.json`
- Test: `tests/test_imdb_parental_guide.py`

**Interfaces:**
- Consumes: `autoposter.collections.charts.GRAPHQL_URL`, `autoposter.collections.charts.HEADERS` (the existing IMDb GraphQL transport constants); `autoposter.providers.fetch.fetch_json(*, method, url, params, request, cache, ttl_seconds)`; `autoposter.providers.cache.ProviderCache`.
- Produces: `IMDbParentalGuideClient(client: httpx.AsyncClient, cache: ProviderCache | None = None, cache_ttl_seconds: int = 24 * 3600)` with `async def categories(self, imdb_id: str) -> list[tuple[str, str, str]] | None`. Each tuple is `(category id, category text, severity text)`, e.g. `("VIOLENCE", "Violence & Gore", "Moderate")`. `None` means "nothing to label" (op scoped out, `categories: null`, or an unrecognised shape) — Task 2 relies on this exact `None`-vs-list distinction.

- [ ] **Step 1: Create the fixture files, verbatim from the probe**

`tests/fixtures/providers/imdb_parental_shawshank.json` — from `p-row85-probe.md`, "Captures, verbatim" → "The Shawshank Redemption" section:

```json
{
  "data": {
    "title": {
      "id": "tt0111161",
      "parentsGuide": {
        "categories": [
          {"category": {"id": "NUDITY", "text": "Sex & Nudity"},
           "severity": {"id": "mildVotes", "text": "Mild"},
           "totalSeverityVotes": 1675},
          {"category": {"id": "VIOLENCE", "text": "Violence & Gore"},
           "severity": {"id": "moderateVotes", "text": "Moderate"},
           "totalSeverityVotes": 868},
          {"category": {"id": "PROFANITY", "text": "Profanity"},
           "severity": {"id": "severeVotes", "text": "Severe"},
           "totalSeverityVotes": 1114},
          {"category": {"id": "ALCOHOL", "text": "Alcohol, Drugs & Smoking"},
           "severity": {"id": "mildVotes", "text": "Mild"},
           "totalSeverityVotes": 801},
          {"category": {"id": "FRIGHTENING", "text": "Frightening & Intense Scenes"},
           "severity": {"id": "moderateVotes", "text": "Moderate"},
           "totalSeverityVotes": 808}
        ]
      }
    }
  }
}
```

`tests/fixtures/providers/imdb_parental_paddington.json` — from the probe's "Paddington" section:

```json
{"data": {"title": {"id": "tt1109624", "parentsGuide": {"categories": [
  {"category": {"id": "NUDITY", "text": "Sex & Nudity"},
   "severity": {"id": "noneVotes", "text": "None"}, "totalSeverityVotes": 113},
  {"category": {"id": "VIOLENCE", "text": "Violence & Gore"},
   "severity": {"id": "mildVotes", "text": "Mild"}, "totalSeverityVotes": 85},
  {"category": {"id": "PROFANITY", "text": "Profanity"},
   "severity": {"id": "noneVotes", "text": "None"}, "totalSeverityVotes": 82},
  {"category": {"id": "ALCOHOL", "text": "Alcohol, Drugs & Smoking"},
   "severity": {"id": "mildVotes", "text": "Mild"}, "totalSeverityVotes": 75},
  {"category": {"id": "FRIGHTENING", "text": "Frightening & Intense Scenes"},
   "severity": {"id": "mildVotes", "text": "Mild"}, "totalSeverityVotes": 79}
]}}}}
```

`tests/fixtures/providers/imdb_parental_null.json` — from the probe's "Null shape" section (`tt0000002`):

```json
{"data": {"title": {"id": "tt0000002",
  "titleText": {"text": "Le clown et ses chiens"},
  "parentsGuide": {"categories": null}}}}
```

- [ ] **Step 2: Write the failing tests**

```python
"""Row 85 -- the IMDb parental-guide client (fetch + parse + cache).

Fixtures are the probe's own verbatim captures (`p-row85-probe.md`,
"Captures, verbatim" and "Null shape" sections) -- pinned, not recalled from
memory, per the module docstring's precedent in `collections/imdb_graphql.py`.

Parsing here is deliberately LENIENT, unlike `collections/imdb_graphql.py`'s
raise-on-drift posture: that module's wrong answer empties a whole live
collection, so it fails loudly. This module's wrong answer is "one item
keeps no parental labels this pass" across a 16k-item library where most
titles have no guide votes at all (`categories: null` is the *common* case,
facts C1.3) -- raising on every shape surprise here would instead crash
metadata operations for the whole item.
"""
import json
from pathlib import Path

import httpx
import pytest

from conftest import session_factory_for

from autoposter.providers.cache import ProviderCache
from autoposter.providers.imdb_parental_guide import IMDbParentalGuideClient

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def test_mixed_severity_profile_parses_every_category():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=load("imdb_parental_shawshank.json")))
    ) as http:
        client = IMDbParentalGuideClient(http)
        result = await client.categories("tt0111161")

    assert result == [
        ("NUDITY", "Sex & Nudity", "Mild"),
        ("VIOLENCE", "Violence & Gore", "Moderate"),
        ("PROFANITY", "Profanity", "Severe"),
        ("ALCOHOL", "Alcohol, Drugs & Smoking", "Mild"),
        ("FRIGHTENING", "Frightening & Intense Scenes", "Moderate"),
    ]


async def test_severity_id_never_appears_in_the_result():
    """severity.text is the contract; severity.id (the mildVotes-shaped
    internal string) must never leak into what this client returns."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=load("imdb_parental_shawshank.json")))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt0111161")

    for _category_id, _category_text, severity_text in result:
        assert severity_text in ("None", "Mild", "Moderate", "Severe")
        assert "Votes" not in severity_text


async def test_none_and_mild_categories_are_both_returned():
    """The client itself does not decide which severities become labels --
    that policy lives in Task 2's writer, not the fetch layer."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=load("imdb_parental_paddington.json")))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt1109624")

    severities = {text for _, _, text in result}
    assert severities == {"None", "Mild"}


async def test_categories_null_returns_none():
    """Facts C1.3: categories: null is the common case (no guide votes), not
    a fault -- skip silently, never raise."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=load("imdb_parental_null.json")))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt0000002")

    assert result is None


async def test_a_completely_unrecognised_body_returns_none_not_a_raise():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"unexpected": "shape"}))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt0000002")

    assert result is None


async def test_a_malformed_single_entry_is_dropped_not_fatal():
    payload = load("imdb_parental_shawshank.json")
    # Corrupt one entry's severity; the other four must still come back.
    payload["data"]["title"]["parentsGuide"]["categories"][0]["severity"] = {"id": "mildVotes"}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as http:
        result = await IMDbParentalGuideClient(http).categories("tt0111161")

    assert len(result) == 4
    assert all(category_id != "NUDITY" for category_id, _, _ in result)


async def test_the_query_is_posted_with_the_id_variable_and_the_imdb_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=load("imdb_parental_shawshank.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await IMDbParentalGuideClient(http).categories("tt0111161")

    assert seen["headers"]["x-imdb-client-name"] == "imdb-web-next"
    assert seen["body"]["variables"] == {"id": "tt0111161"}
    assert "parentsGuide" in seen["body"]["query"]


async def test_repeated_lookups_hit_the_cache_not_the_transport(session):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=load("imdb_parental_shawshank.json"))

    cache = ProviderCache(session_factory_for(session))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = IMDbParentalGuideClient(http, cache=cache, cache_ttl_seconds=3600)
        first = await client.categories("tt0111161")
        second = await client.categories("tt0111161")

    assert first == second
    assert len(calls) == 1, "second lookup should have been served from the cache"


async def test_without_a_cache_every_lookup_hits_the_transport():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=load("imdb_parental_shawshank.json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = IMDbParentalGuideClient(http)
        await client.categories("tt0111161")
        await client.categories("tt0111161")

    assert len(calls) == 2
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_imdb_parental_guide.py -v`
Expected: `ModuleNotFoundError: No module named 'autoposter.providers.imdb_parental_guide'` (or collection error) — the module does not exist yet.

- [ ] **Step 4: Implement the client**

```python
"""IMDb's parental-guide categories for one title (roadmap row 85).

Same transport as ``collections/charts.py`` and ``collections/
imdb_graphql.py`` -- the public, unauthenticated ``api.graphql.imdb.com``
endpoint, the same mandatory ``x-imdb-client-name`` header (its absence gets
a 403 on the chart endpoint). Unlike those two, this goes through
``providers.fetch.fetch_json`` rather than a raw POST: this is a single-shot
per-title lookup with no pagination, and it wants the same cache/decode seam
every other provider client already shares (roadmap row 151's ``decode``
parameter generalised ``fetch_json`` past plain-JSON-over-GET; ``method`` and
``request`` are already caller-supplied, so a POST fits without changes
there).

**The query, verified against the live endpoint on 2026-09-01** (see
``p-row85-probe.md``): ``title(id: "tt…") { parentsGuide { categories {
category { id text } severity { id text } totalSeverityVotes } } } }``.
``severity.text`` is the four-value contract (``None``/``Mild``/``Moderate``/
``Severe``); ``severity.id`` is an internal vote-bucket string
(``mildVotes``, …) and is parsed here only to discard it -- never returned,
never compared.

**Parsing here is deliberately lenient**, the opposite posture from
``collections/imdb_graphql.py``'s raise-on-drift. That module's wrong answer
empties a whole live collection, so failing loudly is the safer choice.
This module's wrong answer costs one item its labels for one pass -- and
``categories: null`` is the *ordinary* result for most of a 16k-item
library (the probe's own finding: an obscure title with no guide votes
answers exactly this shape, indistinguishable at this layer from a title
this endpoint has never heard of -- and that's fine, because our ids come
from facts and are already existence-checked upstream; see roadmap row 85's
C1.3). So every unrecognised shape -- a missing ``title``, a missing
``parentsGuide``, ``categories: null``, a malformed individual entry --
returns ``None`` (or drops just that entry) with a DEBUG line, never a raise.
"""
import logging

import httpx

from autoposter.collections.charts import GRAPHQL_URL, HEADERS
from autoposter.providers.cache import ProviderCache
from autoposter.providers.fetch import fetch_json

logger = logging.getLogger(__name__)

__all__ = ["IMDbParentalGuideClient"]

QUERY = (
    "query TitleParentalGuide($id: ID!) {"
    " title(id: $id) { parentsGuide { categories {"
    " category { id text } severity { id text } totalSeverityVotes"
    " } } } }"
)


def _parse_categories(payload: object, imdb_id: str) -> list[tuple[str, str, str]] | None:
    """``(category id, category text, severity text)`` tuples, or None.

    See the module docstring for why this never raises. Individual malformed
    entries are dropped rather than failing the whole title -- an IMDb
    response holding four good categories and one odd one should not cost
    the item all five.
    """
    if not isinstance(payload, dict):
        logger.debug("IMDb parental guide %r: no payload", imdb_id)
        return None
    title = (payload.get("data") or {}).get("title")
    if not isinstance(title, dict):
        logger.debug("IMDb parental guide %r: no 'title' in the response", imdb_id)
        return None
    guide = title.get("parentsGuide")
    categories = guide.get("categories") if isinstance(guide, dict) else None
    if not isinstance(categories, list):
        logger.debug(
            "IMDb parental guide %r: categories is %r, not a list (no guide "
            "votes, or this id does not exist)", imdb_id, categories,
        )
        return None
    entries: list[tuple[str, str, str]] = []
    for entry in categories:
        category = entry.get("category") if isinstance(entry, dict) else None
        severity = entry.get("severity") if isinstance(entry, dict) else None
        category_id = category.get("id") if isinstance(category, dict) else None
        category_text = category.get("text") if isinstance(category, dict) else None
        severity_text = severity.get("text") if isinstance(severity, dict) else None
        if not (
            isinstance(category_id, str)
            and isinstance(category_text, str)
            and isinstance(severity_text, str)
        ):
            logger.debug(
                "IMDb parental guide %r: dropped a malformed category entry: %r",
                imdb_id, entry,
            )
            continue
        entries.append((category_id, category_text, severity_text))
    return entries


class IMDbParentalGuideClient:
    """One cached ``title(id:)`` lookup per IMDb id."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        cache: ProviderCache | None = None,
        cache_ttl_seconds: int = 24 * 3600,
    ):
        self._client = client
        self._cache = cache
        self._cache_ttl_seconds = cache_ttl_seconds

    async def categories(self, imdb_id: str) -> list[tuple[str, str, str]] | None:
        """This title's parental-guide categories, or None -- see the module
        docstring for every reason it can be None."""
        payload = await fetch_json(
            method="POST",
            url=GRAPHQL_URL,
            params={"id": imdb_id},
            request=lambda: self._client.post(
                GRAPHQL_URL, headers=HEADERS,
                json={"query": QUERY, "variables": {"id": imdb_id}},
            ),
            cache=self._cache,
            ttl_seconds=self._cache_ttl_seconds,
        )
        return _parse_categories(payload, imdb_id)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_imdb_parental_guide.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/autoposter/providers/imdb_parental_guide.py tests/test_imdb_parental_guide.py tests/fixtures/providers/imdb_parental_shawshank.json tests/fixtures/providers/imdb_parental_paddington.json tests/fixtures/providers/imdb_parental_null.json
git commit --no-gpg-sign -m "feat(providers): IMDb parental-guide categories, fetched and cached (row 85 T1)"
```

---

## Task 2: the mass-op wiring (config, label writes, entry-point tests)

**Files:**
- Modify: `src/autoposter/config/schema.py` (`OperationsConfig`, after the row-84 block, before `tmdb_backoff_seconds`)
- Modify: `src/autoposter/plex/writer.py`
- Modify: `src/autoposter/render/pipeline.py` (`apply_metadata`, `process_item`)
- Modify: `src/autoposter/app.py` (`_handle_intent`, startup wiring)
- Modify: `config/autoposter.example.yaml`
- Test: `tests/test_mass_ops_parental_labels.py`

**Interfaces:**
- Consumes: `IMDbParentalGuideClient.categories(imdb_id) -> list[tuple[str, str, str]] | None` (Task 1). `ResolvedItem.imdb_id`, `ResolvedItem.kind` (`plex/client.py`). The existing `plan_edits(item, facts, operations=None)` / `apply_facts(item, facts, operations=None)` (`plex/writer.py`) and `apply_metadata(session, config, media_item_id, item, plex_item, tmdb_facts, mdblist, tvdb=None)` / `process_item(...)` (`render/pipeline.py`).
- Produces: `OperationsConfig.parental_labels_enabled: bool`, `.parental_labels_apply: bool`, `.parental_labels_include_none: bool`. `plex.writer.parental_label_edits(item, categories, operations) -> dict[str, object]` (key `"labels.added"`). `plan_edits(item, facts, operations=None, parental_categories=None)` and `apply_facts(item, facts, operations=None, parental_categories=None)` — both grow one new, defaulted parameter. `render.pipeline._fetch_parental_categories(imdb_parental, item) -> list[tuple[str, str, str]] | None`. `apply_metadata(..., imdb_parental=None)` and `process_item(..., imdb_parental=None)` — both grow one new, defaulted parameter, matching the existing `tvdb=None` pattern.

### Part A — config fields

- [ ] **Step 1: Write the failing config test**

Add to a new file `tests/test_mass_ops_parental_labels.py`:

```python
"""Row 85 -- parental-guide labels as a mass op.

Two STOP-and-file cells, deliberately absent and must stay absent (see the
row close in the roadmap doc): a vote-count floor (the row and its Kometa-
inventory source name no threshold; the probe observed `"Severe"` off a
single vote) and label removal/sync (the row states no removal semantics,
so this op only ever adds).

The last two tests are the gated-feature entry-point test the memory's law
requires: gate-off byte-identical, gate-on fires through the real seam
(`render.pipeline.apply_metadata`), second pass steady (no label churn when
IMDb's answer hasn't changed).
"""
from autoposter.config.schema import OperationsConfig


def test_the_three_fields_default_off():
    operations = OperationsConfig()
    assert operations.parental_labels_enabled is False
    assert operations.parental_labels_apply is False
    assert operations.parental_labels_include_none is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_mass_ops_parental_labels.py -v`
Expected: FAIL — `AttributeError: 'OperationsConfig' object has no attribute 'parental_labels_enabled'`.

- [ ] **Step 3: Add the three fields**

In `src/autoposter/config/schema.py`, insert immediately before the `tmdb_backoff_seconds` field inside `OperationsConfig` (i.e. right after the `originally_available_source` field's closing `)`):

```python
    # Roadmap row 85. IMDb's own parental-guide categories, written as Plex
    # labels. Default OFF -- the family posture rows 86 (backup) and 87
    # (verbs) already use. Two flags, not one, on purpose: `enabled` gates
    # the FETCH (one cached title(id:) GraphQL request per in-scope item --
    # a cost even in dry-run reporting mode), `apply` gates the WRITE, the
    # same split row 87's lock/unlock/remove already draws between "would
    # write" and "wrote."
    parental_labels_enabled: bool = Field(
        default=False,
        description=(
            "Fetch IMDb's parental-guide categories (violence, profanity, "
            "nudity, alcohol, frightening) for each in-scope item and report "
            "which labels would be added. Off makes no request."
        ),
    )
    parental_labels_apply: bool = Field(
        default=False,
        description=(
            "Actually write the fetched parental-guide labels to Plex; off "
            "only reports which labels it would add."
        ),
    )
    parental_labels_include_none: bool = Field(
        default=False,
        description=(
            "Also add a label for a category IMDb's consensus rates 'None', "
            "e.g. 'Alcohol, Drugs & Smoking: None'. Off -- the default -- "
            "labels only Mild/Moderate/Severe categories."
        ),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_mass_ops_parental_labels.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/autoposter/config/schema.py tests/test_mass_ops_parental_labels.py
git commit --no-gpg-sign -m "feat(config): row 85's three parental-label fields, default off"
```

### Part B — the label-write function

- [ ] **Step 6: Write the failing tests**

Append to `tests/test_mass_ops_parental_labels.py`:

```python
from autoposter.plex.writer import parental_label_edits

from test_mass_ops_fields import FakeItem


class LabelledItem(FakeItem):
    """A FakeItem that also carries Plex labels, tag-object shaped."""

    def __init__(self, labels=(), **attrs):
        super().__init__(**attrs)
        self.labels = [_Tag(t) for t in labels]


class _Tag:
    def __init__(self, tag):
        self.tag = tag


CATEGORIES = [
    ("NUDITY", "Sex & Nudity", "Mild"),
    ("VIOLENCE", "Violence & Gore", "Severe"),
    ("PROFANITY", "Profanity", "None"),
]


def test_no_categories_produces_nothing():
    operations = OperationsConfig(parental_labels_apply=True)
    assert parental_label_edits(LabelledItem(), None, operations) == {}
    assert parental_label_edits(LabelledItem(), [], operations) == {}


def test_none_severity_is_excluded_by_default():
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(LabelledItem(), CATEGORIES, operations)
    assert edits == {"labels.added": ["Sex & Nudity: Mild", "Violence & Gore: Severe"]}


def test_none_severity_is_included_when_configured():
    operations = OperationsConfig(parental_labels_apply=True, parental_labels_include_none=True)
    edits = parental_label_edits(LabelledItem(), CATEGORIES, operations)
    assert edits == {
        "labels.added": [
            "Sex & Nudity: Mild", "Violence & Gore: Severe", "Profanity: None",
        ]
    }


def test_a_label_already_on_the_item_is_not_re_added():
    item = LabelledItem(labels=["Sex & Nudity: Mild"])
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(item, CATEGORIES, operations)
    assert edits == {"labels.added": ["Violence & Gore: Severe"]}


def test_the_match_is_casefolded_like_every_other_label_comparison():
    """Plex canonicalises label case -- an exact compare would re-add a
    label already present under different casing every single pass."""
    item = LabelledItem(labels=["sex & nudity: mild"])
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(item, CATEGORIES, operations)
    assert edits == {"labels.added": ["Violence & Gore: Severe"]}


def test_every_label_already_present_produces_no_edit():
    item = LabelledItem(labels=["Sex & Nudity: Mild", "Violence & Gore: Severe"])
    operations = OperationsConfig(parental_labels_apply=True)
    assert parental_label_edits(item, CATEGORIES, operations) == {}


def test_apply_off_reports_and_writes_nothing():
    operations = OperationsConfig()  # parental_labels_apply defaults False
    edits = parental_label_edits(LabelledItem(), CATEGORIES, operations)
    assert edits == {}


def test_severity_id_never_reaches_the_label_text():
    """Belt and braces: even a caller that (wrongly) hands this function
    IMDb's internal severity.id-shaped string must not see it echoed back --
    the label text is built from the tuple's own fields, category text and
    severity TEXT only."""
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(
        LabelledItem(), [("VIOLENCE", "Violence & Gore", "Severe")], operations
    )
    assert "Votes" not in edits["labels.added"][0]
```

- [ ] **Step 7: Run to verify they fail**

Run: `python -m pytest tests/test_mass_ops_parental_labels.py -v`
Expected: FAIL — `ImportError: cannot import name 'parental_label_edits' from 'autoposter.plex.writer'`.

- [ ] **Step 8: Implement `parental_label_edits` and its helpers in `plex/writer.py`**

Add near `_genre_plan` (after `map_values`, before `plan_edits`):

```python
def _current_labels(item) -> dict[str, str]:
    """``{casefolded tag: the tag as the server spells it}`` for one item.

    Mirrors ``collections/reconcile.py``'s ``_folded_labels`` for the same
    reason: Plex canonicalises label case, so an exact compare would add a
    label already present under different casing every single pass.
    """
    return {
        tag.casefold(): tag
        for tag in (getattr(entry, "tag", entry) for entry in getattr(item, "labels", None) or [])
        if isinstance(tag, str)
    }


def _label_text(category_text: str, severity_text: str) -> str:
    """``"Violence & Gore: Severe"``. Not a Kometa-mandated string -- row 85's
    roadmap cell and its Kometa-inventory source name no label format at
    all -- so this is the build's own documented choice (see the row close),
    built only from ``category.text``/``severity.text``, never an id."""
    return f"{category_text}: {severity_text}"


def parental_label_edits(
    item, categories: list[tuple[str, str, str]] | None, operations
) -> dict[str, object]:
    """Row 85: the labels IMDb's parental-guide categories add to this item.

    ``categories`` is ``None`` or ``[]`` for "nothing to label" (see
    ``providers/imdb_parental_guide.py``'s module docstring for every reason)
    and produces no edits either way.

    Each entry is ``(category id, category text, severity text)`` --
    ``severity.text``, never ``severity.id``. A category whose severity is
    ``"None"`` is skipped unless ``operations.parental_labels_include_none``
    says otherwise.

    Additive only, like ``collections/reconcile.py``'s ``_apply_labels``
    without ``label_sync``: this op has no removal semantics stated anywhere
    in its row, so it never strips a label IMDb's guide no longer supports.

    Dry-run by default, the same split row 87's verbs draw: with
    ``parental_labels_apply`` off, a wanted-but-missing label is LOGGED and
    no edit is produced.
    """
    if not categories:
        return {}
    include_none = bool(getattr(operations, "parental_labels_include_none", False))
    wanted = [
        _label_text(category_text, severity_text)
        for _category_id, category_text, severity_text in categories
        if severity_text != "None" or include_none
    ]
    if not wanted:
        return {}
    stored = _current_labels(item)
    missing = [tag for tag in wanted if tag.casefold() not in stored]
    if not missing:
        return {}
    if not getattr(operations, "parental_labels_apply", False):
        logger.info(
            "plex: would add parental-guide label(s) %s to %s "
            "(operations.parental_labels_apply is off)",
            ", ".join(missing), _item_label(item),
        )
        return {}
    return {"labels.added": missing}
```

`parental_label_edits` is defined above `plan_edits`, so `_item_label` (already defined later in the file) is not yet available at that point — move `_item_label`'s definition above `parental_label_edits`, or (simpler, smaller diff) leave `parental_label_edits` where drafted above and rely on Python's late binding: a module-level function body only resolves names at *call* time, not definition time, so `_item_label` being defined later in the same module is fine as long as it exists by the time `parental_label_edits` is actually called. No reordering needed.

- [ ] **Step 9: Run to verify they pass**

Run: `python -m pytest tests/test_mass_ops_parental_labels.py -v`
Expected: PASS (9 tests so far).

- [ ] **Step 10: Commit**

```bash
git add src/autoposter/plex/writer.py tests/test_mass_ops_parental_labels.py
git commit --no-gpg-sign -m "feat(plex): parental-guide labels as an additive, dry-run-by-default write (row 85)"
```

### Part C — wire it into `plan_edits`/`apply_facts` and the label-write batch

- [ ] **Step 11: Write the failing test**

Append to `tests/test_mass_ops_parental_labels.py`:

```python
from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import apply_facts, plan_edits


def test_plan_edits_folds_in_parental_labels():
    item = LabelledItem()
    operations = OperationsConfig(parental_labels_apply=True)
    edits = plan_edits(item, GatheredFacts(), operations, parental_categories=CATEGORIES)
    assert edits["labels.added"] == ["Sex & Nudity: Mild", "Violence & Gore: Severe"]


async def test_apply_facts_calls_addlabel_for_each_addition():
    class RecordingLabelItem(LabelledItem):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.added_labels = []
            self.saved = 0

        def batchEdits(self):  # noqa: N802 - plexapi name
            pass

        def edit(self, **fields):
            pass

        def addLabel(self, tag):  # noqa: N802 - plexapi name
            self.added_labels.append(tag)

        def saveEdits(self):  # noqa: N802 - plexapi name
            self.saved += 1

    item = RecordingLabelItem()
    operations = OperationsConfig(parental_labels_apply=True)
    edits = await apply_facts(item, GatheredFacts(), operations, parental_categories=CATEGORIES)

    assert edits["labels.added"] == ["Sex & Nudity: Mild", "Violence & Gore: Severe"]
    assert item.added_labels == ["Sex & Nudity: Mild", "Violence & Gore: Severe"]
    assert item.saved == 1
```

- [ ] **Step 12: Run to verify it fails**

Run: `python -m pytest tests/test_mass_ops_parental_labels.py -v`
Expected: FAIL — `TypeError: plan_edits() got an unexpected keyword argument 'parental_categories'`.

- [ ] **Step 13: Wire `parental_categories` through `plan_edits` and `apply_facts`**

In `src/autoposter/plex/writer.py`:

```python
def plan_edits(
    item, facts: GatheredFacts, operations=None, parental_categories=None,
) -> dict[str, object]:
```

(update the docstring's `operations` paragraph to add one sentence: `` ``parental_categories`` is the row-85 fetch's result -- ``None``/most callers, in which case this folds in nothing new.``)

At the end of `plan_edits`, right before `return edits`:

```python
    edits.update(verb_edits(item, operations))
    edits.update(parental_label_edits(item, parental_categories, operations))

    return edits
```

Add `_apply_label_edits` next to `_apply_genre_edits`:

```python
def _apply_label_edits(item, additions: list[str]) -> None:
    """Queue label additions via plexapi's documented ``addLabel`` mixin
    method. Must be called after ``item.batchEdits()`` and before
    ``item.saveEdits()``, alongside ``_apply_genre_edits`` -- additions only,
    since ``parental_label_edits`` never produces a removal.
    """
    for tag in additions:
        item.addLabel(tag)
```

Update `apply_facts`:

```python
async def apply_facts(
    item, facts: GatheredFacts, operations=None, parental_categories=None,
) -> dict[str, object]:
    """Write the changed fields in one HTTP call.

    plexapi routes even a single-item edit through the library section, so
    batching the fields together turns several writes into one. Genres and
    parental-guide labels are both queued through their own mixin methods
    (``addGenre``/``removeGenre``, ``addLabel``) rather than ``item.edit()``,
    but still land inside the same ``batchEdits()``/``saveEdits()`` block, so
    it's still a single request.
    """
    edits = plan_edits(item, facts, operations, parental_categories)
    if not edits:
        return {}

    field_edits = {
        k: v for k, v in edits.items() if not k.startswith(("genres.", "labels."))
    }

    def _write() -> None:
        item.batchEdits()
        if field_edits:
            item.edit(**field_edits)
        _apply_genre_edits(item, edits.get("genres.added", []), edits.get("genres.removed", []))
        _apply_label_edits(item, edits.get("labels.added", []))
        item.saveEdits()

    await asyncio.to_thread(_write)
    logger.info("plex: wrote %d field(s) to %s", len(edits), _item_label(item))
    return edits
```

- [ ] **Step 14: Run to verify it passes**

Run: `python -m pytest tests/test_mass_ops_parental_labels.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 15: Run the full writer test suite to check nothing else broke**

Run: `python -m pytest tests/test_mass_ops_fields.py tests/test_mass_ops_verbs.py tests/test_mass_ops_mappers.py -v`
Expected: PASS — `plan_edits`/`apply_facts`'s new parameter is optional and defaults to `None`, which `parental_label_edits` turns into `{}`, so every existing call site (which never passes it) is unaffected.

- [ ] **Step 16: Commit**

```bash
git add src/autoposter/plex/writer.py tests/test_mass_ops_parental_labels.py
git commit --no-gpg-sign -m "feat(plex): batch parental-guide label writes with genres in apply_facts"
```

### Part D — thread the fetch through the render pipeline

- [ ] **Step 17: Write the failing tests**

Append to `tests/test_mass_ops_parental_labels.py`:

```python
import pytest
import pytest_asyncio
from pathlib import Path

from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.render.pipeline import _fetch_parental_categories, apply_metadata

from test_mass_ops_fields import FakeTMDB, _item
from test_mass_ops_verbs import RecordingPlexItem

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


@pytest_asyncio.fixture
async def media_item_id(session):
    media = MediaItem(rating_key="1", library="Movies", kind="movie", title="Heat")
    session.add(media)
    await session.flush()
    return media.id


class FakeImdbParental:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def categories(self, imdb_id):
        self.calls.append(imdb_id)
        return self._result


async def test_fetch_returns_none_without_an_imdb_id():
    client = FakeImdbParental(CATEGORIES)
    result = await _fetch_parental_categories(client, _item(imdb_id=None))
    assert result is None
    assert client.calls == []


async def test_fetch_returns_none_for_a_season():
    client = FakeImdbParental(CATEGORIES)
    result = await _fetch_parental_categories(client, _item(kind="season"))
    assert result is None
    assert client.calls == []


async def test_fetch_delegates_to_the_client_for_a_movie():
    client = FakeImdbParental(CATEGORIES)
    result = await _fetch_parental_categories(client, _item())
    assert result == CATEGORIES
    assert client.calls == ["tt0113277"]


async def test_fetch_returns_none_on_a_transport_error(caplog):
    import httpx as _httpx

    class FailingClient:
        async def categories(self, imdb_id):
            raise _httpx.HTTPError("boom")

    with caplog.at_level("WARNING"):
        result = await _fetch_parental_categories(FailingClient(), _item())
    assert result is None
    assert "IMDb parental-guide request failed" in caplog.text


# --- the gated-feature entry-point test (Global Constraint) -----------------

async def test_entry_point_gate_off_is_byte_identical(session, media_item_id, config):
    # (a) GATE OFF. parental_labels_enabled unset -- apply_metadata must
    # produce exactly the edits it produced before this row existed.
    plex_item = RecordingPlexItem(studio="Warner", locks=[])
    plex_item.labels = []
    imdb_parental = FakeImdbParental(CATEGORIES)

    await apply_metadata(
        session, config, media_item_id, _item(), plex_item,
        FakeTMDB(GatheredFacts()), NullMDBListClient(), imdb_parental=imdb_parental,
    )
    assert plex_item.edits == []
    assert imdb_parental.calls == []  # not even fetched -- enabled is off


async def test_entry_point_gate_on_fires_and_the_second_pass_is_steady(
    session, media_item_id, config
):
    # (b) GATE ON: the fetch and the write both fire through the real seam.
    config.operations.parental_labels_enabled = True
    config.operations.parental_labels_apply = True
    plex_item = RecordingPlexItem(studio="Warner", locks=[])
    plex_item.labels = []
    imdb_parental = FakeImdbParental(CATEGORIES)

    await apply_metadata(
        session, config, media_item_id, _item(), plex_item,
        FakeTMDB(GatheredFacts()), NullMDBListClient(), imdb_parental=imdb_parental,
    )
    assert plex_item.edits[-1] == {
        "labels.added": ["Sex & Nudity: Mild", "Violence & Gore: Severe"]
    }
    assert imdb_parental.calls == ["tt0113277"]

    # (c) SECOND PASS: steady state. The client is asked again (this test
    # double has no cache of its own -- Task 1's ProviderCache is what makes
    # a real second pass free), but the labels are already on the item, so
    # no second label write happens.
    plex_item.labels = [_Tag("Sex & Nudity: Mild"), _Tag("Violence & Gore: Severe")]
    edits_before = len(plex_item.edits)
    await apply_metadata(
        session, config, media_item_id, _item(), plex_item,
        FakeTMDB(GatheredFacts()), NullMDBListClient(), imdb_parental=imdb_parental,
    )
    assert len(plex_item.edits) == edits_before
```

- [ ] **Step 18: Run to verify they fail**

Run: `python -m pytest tests/test_mass_ops_parental_labels.py -v`
Expected: FAIL — `ImportError: cannot import name '_fetch_parental_categories' from 'autoposter.render.pipeline'`.

- [ ] **Step 19: Implement `_fetch_parental_categories` and wire `apply_metadata`/`process_item`**

In `src/autoposter/render/pipeline.py`, add near `apply_metadata`:

```python
async def _fetch_parental_categories(imdb_parental, item) -> list[tuple[str, str, str]] | None:
    """Row 85's fetch, isolated so one IMDb hiccup costs only this item's
    labels, not everything else this pass gathered.

    ``None`` -- for every reason ``parental_label_edits`` treats as "nothing
    to label" -- covers: no client (feature disabled upstream), no IMDb id,
    an item kind IMDb's parental guide is not modelled for (season/episode --
    the guide is a title-level concept, the same scope row 84's TVDb overlay
    already uses), and a transport failure.
    """
    if imdb_parental is None or not item.imdb_id or item.kind not in ("movie", "show"):
        return None
    try:
        return await imdb_parental.categories(item.imdb_id)
    except httpx.HTTPError as exc:
        # The row-84 precedent: one provider's transient failure must not
        # throw away everything else this pass gathered.
        logger.warning("IMDb parental-guide request failed; skipping labels: %s", exc)
        return None
```

Update `apply_metadata`'s signature and body:

```python
async def apply_metadata(
    session: AsyncSession,
    config: Config,
    media_item_id: int,
    item: ResolvedItem,
    plex_item,
    tmdb_facts,
    mdblist,
    tvdb=None,
    imdb_parental=None,
) -> GatheredFacts:
    """Gather this item's facts, store them, and write the changed ones to Plex.

    Runs before any badge rendering, because badges read the values from Plex
    rather than from the providers.
    """
    if not config.operations.enabled:
        return GatheredFacts()

    facts = await gather_facts(
        session, item, tmdb_facts, mdblist, operations=config.operations, tvdb=tvdb
    )
    await persist_facts(session, media_item_id, facts)

    # Row 85. Only fetched when config enables it, so a deployment that never
    # turns this on pays no request and gets today's behaviour exactly.
    parental_categories = None
    if config.operations.parental_labels_enabled:
        parental_categories = await _fetch_parental_categories(imdb_parental, item)

    # Row 87: a verb IS its field's source, so it must fire even when the
    # provider facts are empty -- ``facts.is_empty()`` alone would otherwise
    # skip apply_facts (and every verb with it) on an item no provider has
    # anything to say about. Row 85's fetched categories are the same shape
    # of "the only thing this pass has to write."
    has_verbs = bool(config.operations.field_verbs)
    has_parental = parental_categories is not None
    if (
        config.operations.write_to_plex
        and plex_item is not None
        and (not facts.is_empty() or has_verbs or has_parental)
    ):
        # Row 35. Checked here, at the facts/write seam, and not earlier: the
        # facts above are still gathered and persisted for an exempt item,
        # because the badge stage reads the persisted row rather than this
        # write.
        exempt = exemption_reason(
            config.operations, item.rating_key, item.imdb_id,
            getattr(plex_item, "labels", None),
        )
        if exempt is not None:
            logger.info("plex: skipped writing %s: %s", item.rating_key, exempt)
        else:
            await apply_facts(plex_item, facts, config.operations, parental_categories)
    return facts
```

Update `process_item`'s signature and its call to `apply_metadata`:

```python
async def process_item(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    plex,
    providers: list,
    intent: RenderIntent,
    tmdb_facts=None,
    mdblist=None,
    artwork_probe=None,
    imdb_parental=None,
) -> list[Render]:
```

(add `imdb_parental` to the docstring's parameter list: `` ``imdb_parental`` is row 85's ``IMDbParentalGuideClient``; ``None`` -- what every direct caller and most tests pass -- means the fetch in ``apply_metadata`` is skipped, whatever ``operations.parental_labels_enabled`` says (the same shape ``tvdb=None`` already has for row 84).``)

And in the body, at the existing `apply_metadata` call:

```python
            await apply_metadata(
                session, config, media_item.id, item, plex_item, tmdb_facts, mdblist, tvdb,
                imdb_parental,
            )
```

- [ ] **Step 20: Run to verify they pass**

Run: `python -m pytest tests/test_mass_ops_parental_labels.py -v`
Expected: PASS (all tests).

- [ ] **Step 21: Run the full pipeline/mass-ops suite**

Run: `python -m pytest tests/test_mass_ops_verbs.py tests/test_mass_ops_fields.py tests/test_facts_gather.py tests/test_render_pipeline.py -v`
Expected: PASS — every existing call to `apply_metadata`/`process_item` omits `imdb_parental`, which defaults to `None` and reproduces exactly today's behaviour.

- [ ] **Step 22: Commit**

```bash
git add src/autoposter/render/pipeline.py tests/test_mass_ops_parental_labels.py
git commit --no-gpg-sign -m "feat(pipeline): fetch and gate row 85's parental-guide labels through apply_metadata"
```

### Part E — build the client once at startup and thread it into the queue handler

- [ ] **Step 23: Modify `app.py`**

Near the existing `app.state.mdblist = _build_mdblist(...)` block (after it), add:

```python
        # Row 85. No API key needed -- the endpoint is public and
        # unauthenticated, same as charts.py/imdb_graphql.py -- so this is
        # always built, unlike TVDb/MDBList's credential-gated stand-ins.
        # Whether it is ever asked anything is entirely
        # operations.parental_labels_enabled's call, made per item in
        # render.pipeline.apply_metadata.
        app.state.imdb_parental = IMDbParentalGuideClient(
            http, cache=cache, cache_ttl_seconds=config.providers.cache_ttl_seconds
        )
```

Add the import near the other provider imports at the top of `app.py`:

```python
from autoposter.providers.imdb_parental_guide import IMDbParentalGuideClient
```

Update the `functools.partial(_handle_intent, ...)` call to add `imdb_parental=app.state.imdb_parental` alongside `tmdb_facts=`/`mdblist=`.

Update `_handle_intent`'s signature and its call to `process_item`:

```python
async def _handle_intent(
    session, intent, *, config_holder, http, plex, providers, tmdb_facts=None, mdblist=None,
    artwork_probe=None, imdb_parental=None,
):
    config = config_holder.current
    try:
        await process_item(
            session, config, http, plex, providers, intent,
            tmdb_facts=tmdb_facts, mdblist=mdblist, artwork_probe=artwork_probe,
            imdb_parental=imdb_parental,
        )
```

- [ ] **Step 24: Run the app-wiring test suite**

Run: `python -m pytest tests/test_app.py -v`
Expected: PASS. If `test_app.py` asserts the exact keyword set `_handle_intent`'s partial is built with, update that assertion to include `imdb_parental` — read the failure message first (RED before GREEN even for an incidental break) rather than guessing at the exact assertion text.

- [ ] **Step 25: Commit**

```bash
git add src/autoposter/app.py
git commit --no-gpg-sign -m "feat(app): build the IMDb parental-guide client once and thread it through process_item"
```

### Part F — document the config

- [ ] **Step 26: Add the commented example block**

In `config/autoposter.example.yaml`, right after the row-87 block (`# remove_apply: false`) and before the row-86 block, add:

```yaml
  # Roadmap row 85 -- IMDb's own parental-guide categories, written as Plex
  # labels (e.g. "Violence & Gore: Severe"). `enabled` gates the fetch,
  # `apply` gates the write -- the same split row 87's verbs use.
  # parental_labels_enabled: false
  # parental_labels_apply: false
  # parental_labels_include_none: false
```

- [ ] **Step 27: Run the config-loader smoke test**

Run: `python -m pytest tests/test_config_loader.py tests/test_config_descriptions.py -v`
Expected: PASS — the three new fields each carry a `description`, so `test_config_descriptions.py`'s "every field has a description" rule is satisfied without changes to `config/descriptions.py` (that module walks the schema automatically).

- [ ] **Step 28: Commit**

```bash
git add config/autoposter.example.yaml
git commit --no-gpg-sign -m "docs(config): document row 85's parental-label fields in the example config"
```

---

## Task 3: wrap — full suite, roadmap close, PR body

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` (row 85's cell and the phase-14a risk line)

**Interfaces:**
- Consumes: everything from Tasks 1–2. No new interfaces produced.

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: PASS, 0 failures. This is the T1-measured baseline requirement (facts C2) made concrete: the suite that was green on post-#125 `origin/main` is still green with this branch's changes layered on.

- [ ] **Step 2: Close row 85 in the roadmap**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, change row 85's line (currently `| 85 | `mass_imdb_parental_labels` | IMDb parental-guide labels (violence, profanity, …) — needs parental-guide data the TSV datasets don't carry; GraphQL feasibility is a risk | M | parity-only | — |`) to:

```
| 85 | `mass_imdb_parental_labels` | IMDb parental-guide labels (violence, profanity, …) — needs parental-guide data the TSV datasets don't carry; GraphQL feasibility is a risk | M | parity-only | **closed.** GraphQL feasibility risk retired (`p-row85-probe.md`): `title(id:)` returns all five categories (`NUDITY`/`VIOLENCE`/`PROFANITY`/`ALCOHOL`/`FRIGHTENING`) with a four-value severity contract (`None`/`Mild`/`Moderate`/`Severe`, `severity.text` -- never the internal `severity.id`). Ships as `operations.parental_labels_enabled`/`parental_labels_apply`/`parental_labels_include_none` (default off), fetched via `IMDbParentalGuideClient` (cached, `providers/imdb_parental_guide.py`) and written as additive-only Plex labels (`"{category text}: {severity text}"`, casefold-compared). **Caveat, documented not solved:** no vote-count floor -- the row and its Kometa-inventory source (`mass_imdb_parental_labels (with/without none)`) name no threshold, and the probe observed a category carrying `"Severe"` off a single vote (`tt0000009`, `totalSeverityVotes: 1`); IMDb's consensus is trusted as-is. **STOP-and-filed, not built:** label removal/sync (the row states no removal semantics -- this op only ever adds). **Adjudicated, not specified by the row:** the exact label text format (`"{category}: {severity}"` is this build's own choice, not Kometa's verbatim string, which the roadmap/inventory transcription never records) and the movie/show-only scope (episodes/seasons excluded, matching row 84's TVDb-overlay scope precedent -- IMDb's parental guide is a title-level concept). |
```

Update the phase-14a risk line (`**Risks:** row 85 may be infeasible without scraping beyond the sanctioned IMDb surface — timebox a feasibility spike; if it fails, the row moves to Appendix A with that finding rather than silently disappearing.`) to:

```
**Risks:** row 85's feasibility risk is retired -- `p-row85-probe.md` confirmed the
public GraphQL endpoint carries the full parental-guide shape with no scraping
needed; the row closed with a documented low-N vote-floor caveat rather than
moving to Appendix A.
```

- [ ] **Step 3: Run the roadmap-consistency check, if one exists**

Run: `python -m pytest tests/test_roadmap*.py -v 2>&1 || echo "no roadmap-consistency test file"`
Expected: PASS, or the fallback message if no such test exists in this repo — either is fine; this step exists so the row close is checked the same way row 84/86/87's closes were, not skipped by assumption.

- [ ] **Step 4: Commit the roadmap close**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): row 85 closes -- parental labels shipped, low-N caveat documented"
```

- [ ] **Step 5: Prepare the PR**

Branch: `feat/parental-labels`, cut from post-#125 `origin/main` (this plan assumes Task 1 Step 1 of *execution* — not this plan-writing pass — is where that branch is actually cut, per the facts' C4). PR body, plain, no attribution: summarise what shipped (the client, the two-flag gate, additive casefolded labels), the caveat (no vote floor), and the two STOP-and-file items (label removal/sync; the exact Kometa label string was never specified, so this build's own format is used instead).

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** C1.1 (semantics + STOP-and-file) → Task 3 Step 2's roadmap-close prose. C1.2 (fetch + cache) → Task 1. C1.3 (`categories: null` skip) → Task 1's `_parse_categories`. C1.4 (labels ride existing machinery, default-off with its own apply flag) → Task 2 Parts B/C and the two config flags. C1.5 (`severity.text` not `.id`) → Task 1's parser + two explicit tests, and Task 2's `_label_text`. C2 (fixtures cited by section, MockTransport only, entry-point law, RED-first, T1-measured baseline) → Task 1 Step 1's citations, every test file's transport, Task 2 Part D's three-part test, every step's RED step, Task 3 Step 1. C3 (paperwork) → Task 3. C4 (3 tasks, sizes, branch) → this document's task split.
- **Placeholder scan:** no TBD/TODO,  no "add appropriate handling" phrasing found in the steps above; every code block is complete and runnable against the interfaces this plan itself defines.
- **Type consistency:** `categories`/`parental_categories` is `list[tuple[str, str, str]] | None` everywhere it appears (Task 1's `categories()`, Task 2's `parental_label_edits`, `_fetch_parental_categories`, `plan_edits`/`apply_facts`'s new parameter) — checked across all three tasks for drift and found none.
