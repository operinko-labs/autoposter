# Title-card Plex-preview fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When no provider offers a `title_card` for an episode, fall back to
the preview frame Plex itself derived from the media file — the row-132
(`show_fallback`) twin for episodes, answered by a live probe rather than
guessed.

**Architecture:** One new rung in `render_artifact`, sitting at the exact seam
row 132's season-poster fallback already occupies: after the provider ladder
comes back empty, before the row is recorded `no_art`. The rung asks Plex's
`posters()` listing for the entry the C5 probe identified — `ratingKey`
prefixed `media://`, never the `upload://` entries this service's own past
uploads left behind, and never `_agent_default`'s pick either (that is Plex's
*agent* guess — a `metadata://` local poster or a tmdb/imdb/tvdb hit — a
different thing entirely). The fetch goes through the existing `_download`
(now carrying a `headers` kwarg), which buys #131's full-decode validation
for free. The stored `source_url` is a stable synthetic key
(`plex://<rating_key>/title_card`), never the live, cache-busted Plex thumb
URL, so `base_sha256` alone decides whether to re-render. `source_mode`
becomes `"plex_generated"`, cleared symmetrically with row 132's own
`show_fallback` clear, and a new Action Center flag surfaces it.

**Tech Stack:** Python 3.12+, FastAPI, async SQLAlchemy 2.0 + asyncpg,
PostgreSQL, httpx, plexapi (via the existing `plex/artwork.py` module — no new
plexapi surface, `posters()` is already called elsewhere), pytest +
pytest-asyncio, Docker Compose. **No new runtime dependency, no Alembic
migration, no frontend change.**

---

## Read this first — what governs this plan

1. `.superpowers/sdd/p-preview-fallback-facts.md` — the controller
   adjudications (C1–C5). SETTLED; this plan implements them. Load-bearing
   enough to restate:
   - **C5 — A1 answered.** The operator's live probe (episode 153736,
     `plex.vaderrp.com`) proved `posters()` DOES expose the derived frame: the
     LAST entry, `provider=None`, `ratingKey` prefixed `media://`. The
     selection rule is **the `media://` prefix**, NOT `_agent_default` (which
     picks the first non-`upload://` entry — Plex's *agent* guess, a
     different, wrong thing). No `media://` entry in the listing = no
     derived frame = the existing `no_art` outcome, unchanged.
   - **C1.2 — the self-feeding loop, refuted.** This service uploads its own
     badged card to the very field (`thumb`) a naive read would fetch from.
     Selecting by the `media://` prefix sidesteps this by construction: an
     `upload://` entry (ours or anyone's) can never match, no provenance
     guard needed.
   - **C1.3 — the epoch URL, refuted.** Plex's thumb path carries a
     cache-busting integer that this service's own `lockPoster` bumps every
     pass. The fingerprint must never hash the live URL — it hashes a
     **stable synthetic key**, `plex://<rating_key>/title_card`, stored in
     `render.source_url` unchanged pass to pass. `base_sha256` alone then
     decides re-render.
   - **A6:** `title_card` only. **A2:** lazy `fetch_item`, only for an item
     that actually reaches the rung. **A5:** `follow_redirects=False` on the
     Plex leg. **A4:** row 47's `skipped` short-circuit makes the rung
     unreachable there — left as-is, documented in the rung's own comment.
2. `.superpowers/sdd/p-preview-fallback-recon.md` — the machinery inventory:
   the row-132 seam to copy, `plex/artwork.py`'s existing upload/agent split,
   `_download`'s missing `headers` kwarg, the `source_mode` conventions, the
   flag registry's data-driven shape.
3. **The tree itself.** Every `file:line` below was read at `origin/main`
   `d7d437a` via `git show`, not from the working tree — the working copy
   holds `feat/overlay-ratings`, which is AHEAD of `origin/main` on unrelated
   files (the C2a ratings work). Do not read the mechanism sites from
   whatever branch you are standing on.

---

## Branch and cut point

- Branch name: **`feat/preview-fallback`**.
- **Cut from `origin/main` only after C2a's PR has merged.** At the time this
  plan was written, C2a (`feat/overlay-ratings` — `MDBListClient.ratings`,
  the `plex_*`/imdb/tmdb/user rating aliases) had **not yet** merged into
  `origin/main`. This plan touches `render/pipeline.py`, which C2a's PR also
  touches — cutting early risks a rebase, not a conflict this plan can absorb
  silently. **Step 1 below verifies this with content probes. If the C2a
  markers do not print, STOP, wait for that PR to merge, and re-run Step 1
  later** — do not cut from an older ref and do not guess that it has landed.

---

## Global Constraints

Every task's requirements implicitly include this section.

1. **Scope: `title_card` only (A6).** No change to `poster`, `season_poster`
   or `background` behaviour. Generalising the rung to other kinds is
   speculative and out of scope.
2. **The rung lives in `render_artifact`, at the row-132 seam** — after
   `select_artwork` returns and the `season_poster` fallback block, before
   the `no_art` record. Not in `providers/ladder.py` (no Plex handle there,
   per the recon).
3. **Selection is by the `media://` `ratingKey` prefix, never
   `_agent_default`.** A listing with no `media://` entry is the existing
   `no_art` outcome, unchanged.
4. **The self-feed pin.** An item whose currently-selected thumb is this
   service's own upload (an `upload://` entry, possibly `selected=True`)
   still resolves the `media://` entry, never that upload — by construction
   of the selection rule, not a provenance guard.
5. **The stability pin.** `render.source_url` is the stable synthetic key
   `plex://<rating_key>/title_card`, stored and hashed — never the live,
   epoch-suffixed Plex thumb URL. A bumped epoch must not move the
   fingerprint; changed bytes (a different `base_sha256`) must.
6. **The gate-off pin.** When a provider has `title_card` art, the rung is
   never asked and output is byte-identical to `origin/main` — `source_mode`
   stays `"generate"`.
7. **The fetch goes through `_download`**, gaining a `headers: dict[str, str]
   | None = None` keyword-only parameter (default `None`, so every existing
   call site and every existing test is untouched) and a `follow_redirects:
   bool = True` keyword-only parameter (also defaulting to today's
   behaviour). The Plex leg passes `X-Plex-Token` as a header and
   `follow_redirects=False` (A5).
8. **The token is never logged and never reaches `render.detail`.** Every
   `SourceRefused` raised through this path stays stage-and-class-name-only,
   exactly as `_download`/`_validate_image` already are.
9. **`source_mode = "plex_generated"`** (`String(16)`, 14 characters, no
   migration), set/cleared symmetrically with row 132's own
   `show_fallback`/`generate` pair — copy that exact shape, do not invent a
   new one.
10. **Flag `plex_generated`: `instant=True`, `default_on=True`.** Argued in
    the registry description: every row this fires on already fires
    `missing` today (`status == "no_art"`), so the queue's population does
    not grow, only its labelling improves.
11. **Refuse loudly on a bad frame.** A Plex body that does not decode raises
    `SourceRefused` through `_validate_image`, exactly like every other
    source — caught by `process_item`'s existing per-kind containment,
    recorded `status = "failed"`.
12. **Row 47's asymmetry is left as-is, documented, not restructured.**
    `online_fetch_disabled` returns before the ladder even runs, so the rung
    is unreachable under `disable_online_asset_fetch`. A comment at the rung
    says so; the early return itself is untouched.
13. **The five parity pins and the golden suites stay untouched.**
    `tests/test_badge_parity.py`, `tests/test_production_parity.py`,
    `tests/test_golden.py`, `tests/test_overlay_engine_golden.py`,
    `tests/test_builder_port_golden.py` — this feature only changes where the
    BASE image comes from, never the compositing path, so none of these
    files are touched or need to be.
14. **No migration, no frontend change.** `String(16)` already fits
    `"plex_generated"`; the Action Center's flag registry and `ItemDetail`'s
    `hostOrPath()` already handle a new `source_mode` value and a `plex://…`
    `source_url` with no edit (`frontend/src/api/types.ts:815-819`,
    `frontend/src/pages/ItemDetail.tsx:261-292`).
15. **Container discipline.** One compose project (`pf1`), always with the
    `.superpowers/isolated-db.yml` overlay. Tee output to a path under
    `/app/.superpowers/` inside the container. A run uses **no `--rm`**, is
    started detached (`-d --name`) and waited on with a foreground `docker
    wait`, and the log is read back with `docker cp`. Teardown is `docker
    compose -p pf1 down` — **never** `down -v`.
16. **Commits** are conventional, staged **by name** (never `git add -A`),
    and carry **no AI attribution** of any kind. Same for the PR body. Every
    commit command below is written as a plain `git commit -m "..."`; if
    signing hangs or times out, retry the same command with `--no-gpg-sign`
    appended rather than skipping the commit.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `src/autoposter/plex/artwork.py` | `GENERATED_ARTWORK_PREFIX`, `_generated_default`, `generated_title_card_url` | T1 |
| `src/autoposter/render/pipeline.py` | `_download` gains `headers`/`follow_redirects`; new `fetch_plex_generated_base`; `render_artifact`'s rung, signature, detail and `source_mode` write-back; `process_item`'s signature and call-site | T1 |
| `src/autoposter/db/models.py` | `source_mode` comment gains the `plex_generated` line | T1 |
| `src/autoposter/actions/flags.py` | `_plex_generated` predicate, one registry entry | T1 |
| `src/autoposter/app.py` | `plex_generated_base` partial, threaded through `_handle_intent`/`handler` | T1 |
| `tests/test_plex_artwork.py` | unit tests for the new listing-selection helper | T1 |
| `tests/test_pipeline.py` | `_download` headers/follow_redirects test; five `render_artifact`-level rung tests | T1 |
| `tests/test_pipeline_facts.py` | four `fake_render_artifact` stand-ins gain `**kwargs`; two process_item-level rung tests | T1 |
| `tests/test_action_flags.py` | `plex_generated` fires/silent pin; registry-set test updated | T1 |
| `tests/test_app.py` | two wiring tests, mirroring `artwork_probe`'s own | T1 |
| `docs/research/2026-09-03-plex-episode-posters-probe.md` | new — the operator's probe, banked verbatim | T1 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | new rows 239 (delivered), 240 (filed) | T2 |

---

## Task 1: the rung, the provenance, the flag

**Files:**
- Create: `docs/research/2026-09-03-plex-episode-posters-probe.md`
- Modify: `src/autoposter/plex/artwork.py`
- Modify: `src/autoposter/render/pipeline.py`
- Modify: `src/autoposter/db/models.py`
- Modify: `src/autoposter/actions/flags.py`
- Modify: `src/autoposter/app.py`
- Test: `tests/test_plex_artwork.py`, `tests/test_pipeline.py`,
  `tests/test_pipeline_facts.py`, `tests/test_action_flags.py`,
  `tests/test_app.py`

**Interfaces:**
- Produces: `plex/artwork.generated_title_card_url(plex_item, base_url: str)
  -> str | None` (async); `render/pipeline.fetch_plex_generated_base(http,
  plex, rating_key: str, destination: Path, *, base_url: str, headers:
  dict[str, str], stage: str) -> str | None` (async); `render_artifact(...,
  *, plex_generated_base=None)`; `process_item(..., plex_generated_base=None)`.

- [ ] **Step 1: Verify the cut point and create the branch**

```bash
git fetch origin
git show origin/main:src/autoposter/facts/mdblist.py | grep -q "async def ratings" && echo C2A-RATINGS-PRESENT
git show origin/main:src/autoposter/badges/values.py | grep -q "plex_imdb_rating" && echo C2A-BADGES-PRESENT
git show origin/main:src/autoposter/render/pipeline.py | grep -q 'source_mode = "show_fallback"' && echo ROW132-PRESENT
git show origin/main:src/autoposter/render/pipeline.py | grep -q "_ARTWORK_MAX_PIXELS" && echo ROW131-PRESENT
git show origin/main:src/autoposter/plex/artwork.py | grep -q "_agent_default" && echo AGENT-DEFAULT-PRESENT
git show origin/main:src/autoposter/actions/flags.py | grep -q "_show_fallback" && echo FLAGS-SHOW-FALLBACK-PRESENT
```

Expected: all six markers print. **If `C2A-RATINGS-PRESENT` or
`C2A-BADGES-PRESENT` does not print, STOP** — C2a's PR has not merged into
`origin/main` yet. Do not proceed; wait and re-run this step later. Once all
six print:

```bash
git checkout -b feat/preview-fallback origin/main
git rev-parse HEAD
```

Record the resolved `HEAD` sha in the task report.

- [ ] **Step 2: Bank the probe, and measure the baseline**

Create `docs/research/2026-09-03-plex-episode-posters-probe.md`:

```markdown
# Plex episode posters() — the derived-frame probe

Read-only, plexapi one-liner against the operator's own server
(`plex.vaderrp.com`), 2026-09-03. Listed one episode's `posters()` entries —
keys, provider, selected only. No token in the output below or anywhere in
this file.

This is what answered adjudication A1 (`.superpowers/sdd/p-preview-fallback-facts.md`,
C5): whether `posters()` exposes the frame Plex derives from the media file
itself, and what its listing entry looks like. It does — the **last** entry,
`provider=None`, `ratingKey` prefixed `media://`. That entry is what
`plex/artwork.generated_title_card_url` selects (`_generated_default`,
`GENERATED_ARTWORK_PREFIX = "media://"`) — never the first non-`upload://`
entry (`_agent_default`'s own rule), which the listing below shows is Plex's
**agent** guess (a `metadata://` local poster, then tmdb/imdb/tvdb hits) —
a different, wrong thing to have picked.

The listing also carries **five** `upload://` entries (one `selected=True`,
four orphaned) — this service's own past re-renders, which Plex keeps
appending to rather than replacing. That accumulation is out of this row's
scope; filed as roadmap row 240.

## Raw output

```
episode 153736 'Richard Madeley, Mel Giedroyc, Lee Mack, Simon Amstell' | thumb: /library/metadata/153736/thumb/1788286708
 poster key='/library/metadata/153736/file?url=metadata%3A%2F%2Fposters%2F8c8ff1e60328d2ed3c3eb1903c257d41c0767fe6' provider='local' selected=False ratingKey='metadata://posters/8c8ff1e60328d2ed3c3eb1903c257d41c0767fe6'
 poster key='https://image.tmdb.org/t/p/original/uFn9CRF9bzRCMCbhFEhLlg82els.jpg' provider='tmdb' selected=False ratingKey='https://image.tmdb.org/t/p/original/uFn9CRF9bzRCMCbhFEhLlg82els.jpg'
 poster key='https://m.media-amazon.com/images/M/MV5BNWE0ZDQxZTAtYWE0ZS00OThlLWJiODUtOTYwNTk0YzEwMTBhXkEyXkFqcGc@._V1_.jpg' provider='imdb' selected=False ratingKey='https://m.media-amazon.com/images/M/MV5BNWE0ZDQxZTAtYWE0ZS00OThlLWJiODUtOTYwNTk0YzEwMTBhXkEyXkFqcGc@._V1_.jpg'
 poster key='https://artworks.thetvdb.com/banners/episodes/79556/356117.jpg' provider='tvdb' selected=False ratingKey='https://artworks.thetvdb.com/banners/episodes/79556/356117.jpg'
 poster key='/library/metadata/153736/file?url=upload%3A%2F%2Fposters%2Fseasons%2F1%2Fepisodes%2F1%2Fdf6421f531157330ca30ff2f5ad604edc847e954' provider=None selected=False ratingKey='upload://posters/seasons/1/episodes/1/df6421f531157330ca30ff2f5ad604edc847e954'
 (three more upload:// entries, one selected=True, omitted here — five total)
 poster key='/library/metadata/153736/file?url=media%3A%2F%2F5%2F236feb94b72684905e052dfe82d48a8bce89bb5%2Ebundle%2FContents%2FThumbnails%2Fthumb1%2Ejpg' provider=None selected=False ratingKey='media://5/236feb94b72684905e052dfe82d48a8bce89bb5.bundle/Contents/Thumbnails/thumb1.jpg'
```
```

Then measure the pre-phase baseline — do not assume the number:

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-baseline test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t1-baseline.log'
docker wait pf1-baseline
docker cp pf1-baseline:/app/.superpowers/run-t1-baseline.log .superpowers/run-t1-baseline.log
docker rm pf1-baseline
tail -5 .superpowers/run-t1-baseline.log
```

Expected: a green summary line. Record the measured `BASELINE=<passed>/<skipped>`
in the task report — every later step's suite run is compared against it.

```bash
git add docs/research/2026-09-03-plex-episode-posters-probe.md
git commit -m "docs(research): bank the plex posters() probe that answers A1"
```

- [ ] **Step 3: Write the failing tests for the listing-selection helper**

Append to `tests/test_plex_artwork.py`:

```python
from autoposter.plex.artwork import (
    GENERATED_ARTWORK_PREFIX, _generated_default, generated_title_card_url,
)


class FakePosterEntry:
    def __init__(self, rating_key, key=""):
        self.ratingKey = rating_key
        self.key = key


def test_generated_artwork_prefix_is_media():
    assert GENERATED_ARTWORK_PREFIX == "media://"


def test_generated_default_picks_the_media_prefixed_entry():
    """C5: the media:// entry, never the agent's own guess or an upload."""
    listing = [
        FakePosterEntry("upload://abc", "/x"),
        FakePosterEntry("com.plexapp.agents.themoviedb://1", "/y"),
        FakePosterEntry("media://5/x.bundle/Contents/Thumbnails/thumb1.jpg", "/z"),
    ]
    entry = _generated_default(listing)
    assert entry is not None
    assert entry.ratingKey.startswith("media://")


def test_generated_default_is_none_without_a_media_entry():
    """The self-feed pin's other half: an upload:// entry (ours or anyone
    else's) and an agent guess are both present, but neither is a derived
    frame -- there is nothing here to select."""
    listing = [
        FakePosterEntry("upload://abc", "/x"),
        FakePosterEntry("com.plexapp.agents.themoviedb://1", "/y"),
    ]
    assert _generated_default(listing) is None


def test_generated_default_skips_an_entry_with_no_rating_key():
    assert _generated_default([FakePosterEntry("", "/x")]) is None


async def test_generated_title_card_url_joins_the_entrys_key_to_base_url():
    entry_path = "/library/metadata/1/file?url=media%3A%2F%2F5%2Fx.bundle...thumb1.jpg"
    listing = [FakePosterEntry("media://5/x.bundle/Contents/Thumbnails/thumb1.jpg", entry_path)]

    class FakePlexItem:
        def posters(self):
            return listing

    url = await generated_title_card_url(FakePlexItem(), "http://plex.local/")
    assert url == f"http://plex.local{entry_path}"


async def test_generated_title_card_url_is_none_without_a_media_entry():
    class FakePlexItem:
        def posters(self):
            return [FakePosterEntry("upload://abc", "/x")]

    assert await generated_title_card_url(FakePlexItem(), "http://plex.local/") is None
```

- [ ] **Step 4: Run the new tests and confirm they fail on import**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s3 test sh -c 'pytest -q tests/test_plex_artwork.py 2>&1 | tee /app/.superpowers/run-t1-s3.log'
docker wait pf1-t1s3
docker cp pf1-t1s3:/app/.superpowers/run-t1-s3.log .superpowers/run-t1-s3.log
docker rm pf1-t1s3
tail -20 .superpowers/run-t1-s3.log
```

Expected: `ImportError: cannot import name 'GENERATED_ARTWORK_PREFIX'` (or
similar) — the names do not exist yet.

- [ ] **Step 5: Implement the listing-selection helper**

In `src/autoposter/plex/artwork.py`, immediately after `_agent_default`
(which ends `return None`, just before `def reset_artwork_to_agent_default`),
insert:

```python
# How Plex keys a frame it derived from the media file itself, rather than a
# user upload or an agent's own guess -- the shape the live probe against
# episode 153736 confirmed (docs/research/2026-09-03-plex-episode-posters-
# probe.md): the LAST posters() entry, provider=None, ratingKey
# 'media://<bundle-hash>.bundle/Contents/Thumbnails/thumb1.jpg'.
GENERATED_ARTWORK_PREFIX = "media://"


def _generated_default(listing):
    """The entry in a ``posters()`` listing that Plex derived from the media file.

    ``None`` when no such entry exists. This is deliberately NOT
    ``_agent_default``: the probe that settled adjudication A1 found the
    first non-``upload://`` entry is Plex's own *agent* guess (a
    ``metadata://`` local poster, or a tmdb/imdb/tvdb hit) -- never the frame
    Plex captured from the video stream itself. That frame is a *different*
    entry, keyed ``media://...``, and it is what this selects instead.
    ``ratingKey`` is read defensively and an entry without one is skipped,
    not returned, the same defensive shape ``_agent_default`` uses.
    """
    for entry in listing:
        rating_key = getattr(entry, "ratingKey", "") or ""
        if rating_key.startswith(GENERATED_ARTWORK_PREFIX):
            return entry
    return None


async def generated_title_card_url(plex_item, base_url: str) -> str | None:
    """The absolute URL of Plex's own generated title-card frame, or ``None``.

    Scoped to ``title_card`` only (the plex-preview fallback's own scope,
    adjudication A6) -- unlike ``_artwork_url`` this does not route on
    ``art_kind`` through ``PLEX_ART_FIELDS``: nothing else calls it yet, and
    a parameter with exactly one legal value is not a parameter.

    Blocking: ``posters()`` is an HTTP GET through plexapi. Offloaded like
    every other plexapi field or listing read in this module.
    """
    entries = await asyncio.to_thread(plex_item.posters)
    entry = _generated_default(entries)
    if entry is None:
        return None
    path = getattr(entry, "key", "") or ""
    if not path:
        return None
    return f"{base_url.rstrip('/')}{path}"
```

- [ ] **Step 6: Run the tests again and confirm they pass**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s6 test sh -c 'pytest -q tests/test_plex_artwork.py 2>&1 | tee /app/.superpowers/run-t1-s6.log'
docker wait pf1-t1s6
docker cp pf1-t1s6:/app/.superpowers/run-t1-s6.log .superpowers/run-t1-s6.log
docker rm pf1-t1s6
tail -10 .superpowers/run-t1-s6.log
```

Expected: all `test_plex_artwork.py` tests pass, including the new ones.

```bash
git add src/autoposter/plex/artwork.py tests/test_plex_artwork.py
git commit -m "feat(plex): select the media://-prefixed posters() entry, not the agent's own guess"
```

- [ ] **Step 7: Write the failing test for `_download`'s new kwargs**

Add to `tests/test_pipeline.py`, near the top-level helpers (after
`_fake_http`):

```python
async def test_download_forwards_headers_and_follow_redirects(tmp_path):
    seen = {}

    async def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, content=decodable_png())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        destination = tmp_path / "base.jpg"
        sha = await pipeline_module._download(
            http, "https://plex.local/x.jpg", destination, stage="the title_card source",
            headers={"X-Plex-Token": "tok"}, follow_redirects=False,
        )

    assert sha
    assert seen["headers"]["x-plex-token"] == "tok"


async def test_download_headers_default_to_none_and_redirects_default_to_true(tmp_path):
    """Every pre-existing call site passes neither kwarg -- this pins that the
    defaults reproduce today's behaviour exactly."""
    import inspect

    signature = inspect.signature(pipeline_module._download)
    assert signature.parameters["headers"].default is None
    assert signature.parameters["follow_redirects"].default is True
```

- [ ] **Step 8: Run it and confirm it fails**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s8 test sh -c 'pytest -q tests/test_pipeline.py -k download_forwards_or_defaults 2>&1 | tee /app/.superpowers/run-t1-s8.log'
docker wait pf1-t1s8
docker cp pf1-t1s8:/app/.superpowers/run-t1-s8.log .superpowers/run-t1-s8.log
docker rm pf1-t1s8
tail -20 .superpowers/run-t1-s8.log
```

If the `-k` filter matches nothing (no `or` in each test's name — this is
one test with an `_and_` in it), run with `-k download` instead. Expected:
`TypeError: _download() got an unexpected keyword argument 'headers'`.

- [ ] **Step 9: Add the `headers`/`follow_redirects` kwargs to `_download`**

In `src/autoposter/render/pipeline.py`, change the signature:

```python
async def _download(
    http: httpx.AsyncClient, url: str, destination: Path, *, stage: str
) -> str:
```

to:

```python
async def _download(
    http: httpx.AsyncClient, url: str, destination: Path, *, stage: str,
    headers: dict[str, str] | None = None, follow_redirects: bool = True,
) -> str:
```

Append to the docstring, just before its closing `"""` (after `` ``stage``
names what is being fetched, for the refusal message.``):

```python

    ``headers`` and ``follow_redirects`` are both keyword-only and default to
    the values every existing call site already gets (no headers, redirects
    followed), so nothing already calling this changes. The plex-preview
    fallback (roadmap row 239) is the first caller to pass either: an
    ``X-Plex-Token`` header, and ``follow_redirects=False`` (adjudication A5)
    because a custom auth header is not one httpx strips on a cross-origin
    redirect, and PMS never needs to redirect an image blob anyway.
```

And change the stream call:

```python
        async with http.stream("GET", url, follow_redirects=True) as response:
```

to:

```python
        async with http.stream(
            "GET", url, follow_redirects=follow_redirects, headers=headers
        ) as response:
```

- [ ] **Step 10: Run the tests again and confirm they pass**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s10 test sh -c 'pytest -q tests/test_pipeline.py 2>&1 | tee /app/.superpowers/run-t1-s10.log'
docker wait pf1-t1s10
docker cp pf1-t1s10:/app/.superpowers/run-t1-s10.log .superpowers/run-t1-s10.log
docker rm pf1-t1s10
tail -10 .superpowers/run-t1-s10.log
```

Expected: all of `test_pipeline.py` passes — this is also the first proof
that adding the two kwargs did not disturb any existing `_download` call
site (all of which still pass no `headers`/`follow_redirects` at all).

```bash
git add src/autoposter/render/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): _download can carry a token header and skip redirects"
```

- [ ] **Step 11: Write the failing tests for `fetch_plex_generated_base`**

Add to `tests/test_pipeline.py`:

```python
class _FakeGeneratedEntry:
    def __init__(self, rating_key, key):
        self.ratingKey = rating_key
        self.key = key


class _FakeGeneratedPlexItem:
    """A plex_item stand-in whose posters() answers a fixed listing -- the
    shape the live probe recorded (upload:// entries plus one media://)."""

    def __init__(self, listing):
        self._listing = listing

    def posters(self):
        return self._listing


class _FakePlexForGenerated:
    def __init__(self, plex_item, expected_rating_key="900"):
        self._plex_item = plex_item
        self._expected_rating_key = expected_rating_key

    async def fetch_item(self, rating_key):
        assert rating_key == self._expected_rating_key
        return self._plex_item


GENERATED_LISTING_NO_SELF_FEED = [
    _FakeGeneratedEntry("metadata://posters/x", "/library/metadata/900/file?url=metadata..."),
    _FakeGeneratedEntry(
        "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
        "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
    ),
]

GENERATED_LISTING_WITH_SELF_FEED = [
    _FakeGeneratedEntry("upload://abc123", "/library/metadata/900/file?url=upload..."),
    _FakeGeneratedEntry(
        "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
        "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
    ),
]


async def test_fetch_plex_generated_base_downloads_the_media_entry(tmp_path):
    plex_item = _FakeGeneratedPlexItem(GENERATED_LISTING_NO_SELF_FEED)
    plex = _FakePlexForGenerated(plex_item)
    seen = {}

    async def handler(request):
        seen["headers"] = dict(request.headers)
        seen["url"] = str(request.url)
        return httpx.Response(200, content=decodable_png())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        destination = tmp_path / "base.jpg"
        sha = await pipeline_module.fetch_plex_generated_base(
            http, plex, "900", destination,
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
            stage="the title_card source",
        )

    assert sha
    assert destination.exists()
    assert seen["headers"]["x-plex-token"] == "tok"
    assert "media" in seen["url"] or "thumb1" in seen["url"]


async def test_fetch_plex_generated_base_ignores_our_own_upload(tmp_path):
    """The self-feed pin: a listing with an upload:// entry (ours) selected
    ahead of the media:// entry still resolves the media:// frame."""
    plex_item = _FakeGeneratedPlexItem(GENERATED_LISTING_WITH_SELF_FEED)
    plex = _FakePlexForGenerated(plex_item)

    async def handler(request):
        assert "upload" not in str(request.url)
        return httpx.Response(200, content=decodable_png())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        sha = await pipeline_module.fetch_plex_generated_base(
            http, plex, "900", tmp_path / "base.jpg",
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
            stage="the title_card source",
        )

    assert sha


async def test_fetch_plex_generated_base_is_none_without_a_media_entry(tmp_path):
    plex_item = _FakeGeneratedPlexItem([
        _FakeGeneratedEntry("upload://abc123", "/library/metadata/900/file?url=upload..."),
    ])
    plex = _FakePlexForGenerated(plex_item)

    async def unreachable(request):
        raise AssertionError("no media:// entry -- _download must not be called")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http:
        sha = await pipeline_module.fetch_plex_generated_base(
            http, plex, "900", tmp_path / "base.jpg",
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
            stage="the title_card source",
        )

    assert sha is None
```

- [ ] **Step 12: Run the tests and confirm they fail**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s12 test sh -c 'pytest -q tests/test_pipeline.py -k fetch_plex_generated_base 2>&1 | tee /app/.superpowers/run-t1-s12.log'
docker wait pf1-t1s12
docker cp pf1-t1s12:/app/.superpowers/run-t1-s12.log .superpowers/run-t1-s12.log
docker rm pf1-t1s12
tail -20 .superpowers/run-t1-s12.log
```

Expected: `AttributeError: module 'autoposter.render.pipeline' has no
attribute 'fetch_plex_generated_base'`.

- [ ] **Step 13: Implement `fetch_plex_generated_base`**

In `src/autoposter/render/pipeline.py`, change the import line:

```python
from autoposter.plex.artwork import upload_artwork
```

to:

```python
from autoposter.plex.artwork import generated_title_card_url, upload_artwork
```

Then, immediately after `_download`'s closing `return digest.hexdigest()`
and before `async def _upsert_media_item`, insert:

```python
async def fetch_plex_generated_base(
    http: httpx.AsyncClient, plex, rating_key: str, destination: Path,
    *, base_url: str, headers: dict[str, str], stage: str,
) -> str | None:
    """Download Plex's own generated title-card frame, or answer ``None``.

    The plex-preview fallback (roadmap row 239): lazily fetches the plexapi
    item for ``rating_key`` (adjudication A2 -- only an episode whose
    provider ladder came back empty ever reaches this, so this is not a
    second fetch for every item ``process_item`` already handles), lists its
    posters, and downloads the ``media://``-prefixed entry
    (``generated_title_card_url``) -- never an ``upload://`` entry, which is
    our own previous output locked onto the same field (the self-feed loop
    C1.2 refutes). ``None`` when the listing has no such entry, the caller's
    cue to fall through to the existing ``no_art`` outcome.

    Goes through ``_download``, not a bare GET, so the fetch gets #131's
    full-decode validation and the byte cap for free.
    ``follow_redirects=False`` (adjudication A5): ``X-Plex-Token`` is a
    custom header httpx will not strip on a cross-origin redirect, and PMS
    never needs one for an image blob anyway.

    Built as a ``functools.partial`` at app.py's composition time, with
    ``http``, ``plex``, ``base_url`` and the token header baked in --
    ``render_artifact`` calls the result with only ``rating_key`` and
    ``destination``, so the token is never in scope there at all.
    """
    plex_item = await plex.fetch_item(rating_key)
    url = await generated_title_card_url(plex_item, base_url)
    if url is None:
        return None
    return await _download(
        http, url, destination, stage=stage, headers=headers, follow_redirects=False,
    )
```

- [ ] **Step 14: Run the tests again and confirm they pass**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s14 test sh -c 'pytest -q tests/test_pipeline.py 2>&1 | tee /app/.superpowers/run-t1-s14.log'
docker wait pf1-t1s14
docker cp pf1-t1s14:/app/.superpowers/run-t1-s14.log .superpowers/run-t1-s14.log
docker rm pf1-t1s14
tail -10 .superpowers/run-t1-s14.log
```

Expected: all of `test_pipeline.py` passes.

```bash
git add src/autoposter/render/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): fetch_plex_generated_base, the download half of the rung"
```

- [ ] **Step 15: Write the failing `render_artifact`-level rung tests**

Add to `tests/test_pipeline.py`, after the season-poster fallback block
(after `test_season_art_appearing_later_re_renders_and_clears_the_fallback`,
before `test_library_language_overrides_are_per_library_and_per_art_kind`):

```python
import functools

TITLE_CARD_URL = "https://img/title-card.jpg"


class _TitleCardAwareProvider:
    """Serves a title_card candidate only when configured to."""

    name = "TMDB"

    def __init__(self, *, has_art: bool):
        self._has_art = has_art
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if request.art_kind == "title_card" and self._has_art:
            return [ArtCandidate("TMDB", TITLE_CARD_URL, None, 1920, 1080, 5.0)]
        return []


def _episode_item():
    return item(kind="episode", title="Chapter One", season=1, episode=1, root="Severance (2022)")


def _plex_generated_base_for(http, listing, rating_key="1"):
    plex_item = _FakeGeneratedPlexItem(listing)
    plex = _FakePlexForGenerated(plex_item, expected_rating_key=rating_key)
    return functools.partial(
        pipeline_module.fetch_plex_generated_base, http, plex,
        base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
    )


async def test_title_card_gate_off_when_a_provider_has_art(session, tmp_path, monkeypatch):
    """The gate-off pin: a provider offering a title card is untouched, and
    the Plex rung is never even asked."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    provider = _TitleCardAwareProvider(has_art=True)

    async def unreachable(*args, **kwargs):
        raise AssertionError("the Plex rung must not run when a provider has art")

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _episode_item(), "title_card", [provider],
            plex_generated_base=unreachable,
        )

    assert render.status == "rendered"
    assert render.source_url == TITLE_CARD_URL
    assert render.source_mode == "generate"


async def test_title_card_falls_back_to_plexs_generated_frame(session, tmp_path, monkeypatch):
    """No provider has a title card; Plex's own derived frame (the media://
    entry, never the agent guess -- C5) becomes the base."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    provider = _TitleCardAwareProvider(has_art=False)
    resolved = _episode_item()

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, resolved, "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, GENERATED_LISTING_NO_SELF_FEED),
        )

    assert render.status == "rendered"
    assert render.source_url == f"plex://{resolved.rating_key}/title_card"
    assert render.source_mode == "plex_generated"
    assert render.provider == "plex"
    assert render.provider_rank is None
    assert render.selected_language is None
    assert "Plex's generated frame" in (render.detail or "")


async def test_title_card_stays_no_art_when_plex_has_no_generated_frame(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    provider = _TitleCardAwareProvider(has_art=False)
    listing_without_generated = [
        _FakeGeneratedEntry("upload://abc123", "/library/metadata/1/file?url=upload..."),
        _FakeGeneratedEntry("com.plexapp.agents.themoviedb://1", "https://image.tmdb.org/x.jpg"),
    ]

    async with _fake_http() as http:
        render = await render_artifact(
            session, config, http, _episode_item(), "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, listing_without_generated),
        )

    assert render.status == "no_art"
    assert render.detail == "no title_card art on any provider"
    assert render.source_mode == "generate"
    assert not Path(config.assets_root).exists()


async def test_title_card_art_appearing_later_re_renders_and_clears_the_fallback(
    session, tmp_path, monkeypatch
):
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _episode_item()

    async with _fake_http() as http:
        first = await render_artifact(
            session, config, http, resolved, "title_card",
            [_TitleCardAwareProvider(has_art=False)],
            plex_generated_base=_plex_generated_base_for(http, GENERATED_LISTING_NO_SELF_FEED),
        )
        assert first.source_mode == "plex_generated"

        second = await render_artifact(
            session, config, http, resolved, "title_card",
            [_TitleCardAwareProvider(has_art=True)],
            plex_generated_base=_plex_generated_base_for(http, GENERATED_LISTING_NO_SELF_FEED),
        )

    assert second.fingerprint != first.fingerprint
    assert second.source_url == TITLE_CARD_URL
    assert second.source_mode == "generate"


async def test_title_card_fingerprint_is_stable_across_a_bumped_plex_thumb_epoch(
    session, tmp_path, monkeypatch
):
    """The stability pin (C1.3): the key Plex serves the frame from can
    change (our own lockPoster bumps its epoch every pass) without moving the
    fingerprint -- only the BYTES (base_sha256) decide re-render, because
    source_url is the stable synthetic key, never the live URL."""
    config = _logo_test_config(tmp_path)
    _stub_out_imagemagick(monkeypatch)
    resolved = _episode_item()
    provider = _TitleCardAwareProvider(has_art=False)
    listing_epoch_1 = [
        _FakeGeneratedEntry(
            "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
            "/library/metadata/1/file?url=media%3A%2F%2F5%2Fx.bundle...&epoch=1",
        ),
    ]
    listing_epoch_2 = [
        _FakeGeneratedEntry(
            "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
            "/library/metadata/1/file?url=media%3A%2F%2F5%2Fx.bundle...&epoch=2",
        ),
    ]

    async with _fake_http() as http:
        first = await render_artifact(
            session, config, http, resolved, "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, listing_epoch_1),
        )
        second = await render_artifact(
            session, config, http, resolved, "title_card", [provider],
            plex_generated_base=_plex_generated_base_for(http, listing_epoch_2),
        )

    assert second.fingerprint == first.fingerprint
    assert second.detail == "unchanged"
```

- [ ] **Step 16: Run the tests and confirm they fail**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s16 test sh -c 'pytest -q tests/test_pipeline.py -k title_card 2>&1 | tee /app/.superpowers/run-t1-s16.log'
docker wait pf1-t1s16
docker cp pf1-t1s16:/app/.superpowers/run-t1-s16.log .superpowers/run-t1-s16.log
docker rm pf1-t1s16
tail -30 .superpowers/run-t1-s16.log
```

Expected: `TypeError: render_artifact() got an unexpected keyword argument
'plex_generated_base'` on every new test.

- [ ] **Step 17: Wire the rung into `render_artifact`**

In `src/autoposter/render/pipeline.py`, change `render_artifact`'s signature:

```python
async def render_artifact(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    item: ResolvedItem,
    art_kind: str,
    providers: list,
) -> Render:
    """Build one artifact. Idempotent: safe to run repeatedly for the same item."""
```

to:

```python
async def render_artifact(
    session: AsyncSession,
    config: Config,
    http: httpx.AsyncClient,
    item: ResolvedItem,
    art_kind: str,
    providers: list,
    *,
    plex_generated_base=None,
) -> Render:
    """Build one artifact. Idempotent: safe to run repeatedly for the same item.

    ``plex_generated_base`` is the plex-preview fallback's own hook (roadmap
    row 239): an async callable ``(rating_key, destination, *, stage) -> str
    | None`` -- ``fetch_plex_generated_base`` bound at app.py's composition
    time via ``functools.partial`` with ``http``, ``plex``, ``base_url`` and
    the ``X-Plex-Token`` header baked in, so this function never sees the
    token. ``None`` (every existing call site, every existing test) means the
    rung simply never runs and ``title_card`` behaves exactly as it does on
    main.
    """
```

Then find the season-poster fallback block and the lines right after it:

```python
                show_fallback = selection.candidate is not None
            if selection.candidate is None:
                render.status = "no_art"
                render.detail = f"no {art_kind} art on any provider"
                await session.commit()
                return render
            candidate = selection.candidate
            chosen_candidate = candidate
            base_sha = await _download(
                http, candidate.url, working, stage=f"the {art_kind} source"
            )
            source_url = candidate.url
            provider_name = candidate.provider
            textless = candidate.is_textless
            # True exactly when the order preferred textless art, no provider
            # had any, and the ladder took a text-bearing image rather than
            # nothing. The ladder has returned this since it was written and
            # nothing has ever read it.
            textless_fallback = selection.is_fallback
```

Replace it with:

```python
                show_fallback = selection.candidate is not None
            # The plex-preview fallback (roadmap row 239), title_card's own
            # twin of the season_poster rung just above -- and the same seam:
            # after the ladder, before the no_art record. Unreachable when
            # online_fetch_disabled() already returned above (adjudication
            # A4): row 47 scopes itself to "no provider requests", and this
            # reads Plex's own server rather than a provider, but the rung
            # sits after that gate anyway rather than being carved out of it,
            # so a deployment running with fetch disabled sees no change from
            # main. Named here rather than restructuring that early return.
            plex_generated = False
            if (
                selection.candidate is None
                and art_kind == "title_card"
                and plex_generated_base is not None
            ):
                plex_base_sha = await plex_generated_base(
                    item.rating_key, working, stage="the title_card source",
                )
                plex_generated = plex_base_sha is not None
            if selection.candidate is None and not plex_generated:
                render.status = "no_art"
                render.detail = f"no {art_kind} art on any provider"
                await session.commit()
                return render
            if plex_generated:
                # The synthetic, STABLE key -- never the live Plex thumb URL,
                # which carries a cache-busting epoch our own uploadPoster/
                # lockPoster bumps on every pass (the epoch-URL refutation,
                # C1.3). Storing and hashing this same string (the write-back
                # below reuses this variable) is what keeps a bumped epoch
                # from moving the fingerprint while regenerated bytes still
                # do, through base_sha alone -- and what keeps
                # config/impact.py's recompute honest, since it reads this
                # same stored column back.
                base_sha = plex_base_sha
                source_url = f"plex://{item.rating_key}/title_card"
                provider_name = "plex"
                textless = None
            else:
                candidate = selection.candidate
                chosen_candidate = candidate
                base_sha = await _download(
                    http, candidate.url, working, stage=f"the {art_kind} source"
                )
                source_url = candidate.url
                provider_name = candidate.provider
                textless = candidate.is_textless
                # True exactly when the order preferred textless art, no
                # provider had any, and the ladder took a text-bearing image
                # rather than nothing. The ladder has returned this since it
                # was written and nothing has ever read it.
                textless_fallback = selection.is_fallback
```

Then find the write-back's `render.detail` assignment:

```python
    render.detail = (
        "no season_poster art on any provider; styled the show's poster instead"
        if show_fallback
        else None
    )
```

Replace with:

```python
    render.detail = (
        "no season_poster art on any provider; styled the show's poster instead"
        if show_fallback
        else "no title_card art on any provider; used Plex's generated frame instead"
        if plex_generated
        else None
    )
```

Then find the `source_mode` set/clear immediately below it:

```python
    if show_fallback:
        render.source_mode = "show_fallback"
    elif render.source_mode == "show_fallback":
        render.source_mode = "generate"
```

Replace with:

```python
    if show_fallback:
        render.source_mode = "show_fallback"
    elif render.source_mode == "show_fallback":
        render.source_mode = "generate"
    # The plex-preview fallback's own twin of the block above -- row 239,
    # cleared the same symmetric way row 132 clears show_fallback.
    if plex_generated:
        render.source_mode = "plex_generated"
    elif render.source_mode == "plex_generated":
        render.source_mode = "generate"
```

- [ ] **Step 18: Run the tests again and confirm they pass**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s18 test sh -c 'pytest -q tests/test_pipeline.py 2>&1 | tee /app/.superpowers/run-t1-s18.log'
docker wait pf1-t1s18
docker cp pf1-t1s18:/app/.superpowers/run-t1-s18.log .superpowers/run-t1-s18.log
docker rm pf1-t1s18
tail -10 .superpowers/run-t1-s18.log
```

Expected: all of `test_pipeline.py` passes, including every new
`title_card`-prefixed test and the pre-existing `season_poster` fallback
tests (the gate-off pin's proof at the `season_poster` level too — untouched).

```bash
git add src/autoposter/render/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): the title_card rung asks Plex when no provider has art"
```

- [ ] **Step 19: Wire `process_item`, add the process_item-level tests, fix the four existing fakes**

In `src/autoposter/render/pipeline.py`, change `process_item`'s signature:

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

to:

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
    plex_generated_base=None,
) -> list[Render]:
```

Add one paragraph to its docstring, after the existing "A failure anywhere in
this step is caught and logged..." paragraph — the docstring's last paragraph
before the closing `"""` (the `artwork_probe` paragraph comes earlier, before
`imdb_parental`'s own paragraph; this new one goes after both, immediately
before the close):

```python

    ``plex_generated_base`` is passed straight to ``render_artifact``; see
    its own docstring for what it does and why it is optional.
    """
```

(Only add the new paragraph text and keep the existing closing `"""` — do not
duplicate it.)

Then find the artifact loop's call to `render_artifact`:

```python
            results.append(
                await render_artifact(session, config, http, item, art_kind, providers)
            )
```

Replace with:

```python
            results.append(
                await render_artifact(
                    session, config, http, item, art_kind, providers,
                    plex_generated_base=plex_generated_base,
                )
            )
```

Now the four existing `fake_render_artifact` stand-ins in
`tests/test_pipeline_facts.py` break, because `process_item` now always
passes `plex_generated_base=` as a keyword argument and these fixed
6-parameter fakes do not accept it. Fix all four (lines with `async def
fake_render_artifact(session, config, http, item, art_kind, providers):` or
`(session_, config, http, item, art_kind, providers):`) by appending
`**_kwargs` to each:

```python
    async def fake_render_artifact(session, config, http, item, art_kind, providers, **_kwargs):
```

(and the one using `session_` as its first parameter name — same fix,
`**_kwargs` appended, name unchanged otherwise). There are four such
functions in this file (`test_metadata_failure_does_not_block_artwork`,
`test_metadata_db_error_still_lets_artwork_use_the_session`,
`test_metadata_runs_before_the_artifact_loop`,
`test_a_plex_without_fetch_item_is_not_swallowed`) — the fifth,
`test_cancelled_error_during_metadata_still_propagates`'s
`fake_render_artifact(*args, **kwargs)`, already accepts any kwargs and
needs no change.

Then add two more tests to `tests/test_pipeline_facts.py`, which exercise the
rung through `process_item` itself — the real entry point, per the
gated-features law (memory: two prior same-branch defects where helper tests
passed and the wired path differed):

```python
async def test_title_card_self_feed_is_refused_through_process_item(session):
    """Finding #1's regression test, run through process_item -- the real
    entry point. An episode whose thumb is already our own badged output
    still resolves the media:// frame, never the upload:// entry Plex is
    currently showing."""
    import functools

    from autoposter.providers.base import ArtCandidate
    from autoposter.render.pipeline import fetch_plex_generated_base

    class _FakeEntry:
        def __init__(self, rating_key, key):
            self.ratingKey = rating_key
            self.key = key

    class _FakePlexItem:
        def posters(self):
            return [
                _FakeEntry("upload://abc123", "/library/metadata/900/file?url=upload..."),
                _FakeEntry(
                    "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
                    "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
                ),
            ]

    class _FakePlex:
        async def resolve(self, intent):
            return ResolvedItem(
                rating_key="900", library="Severance (2022)", kind="episode",
                title="Chapter One", year=2022, season_number=1, episode_number=1,
                root_folder="Severance (2022)", file_path="/mnt/Media/x.mkv",
                art_url=None, tmdb_id=1, tvdb_id=None, imdb_id=None,
            )

        async def fetch_item(self, rating_key):
            return _FakePlexItem()

    class _NoArtProvider:
        name = "TMDB"

        async def fetch(self, request):
            return []

    async def handler(request):
        assert "upload" not in str(request.url)
        return httpx.Response(200, content=decodable_png())

    config = load_config(EXAMPLE)
    config.badges.enabled = False

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        plex_generated_base = functools.partial(
            fetch_plex_generated_base, http, _FakePlex(),
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
        )
        results = await pipeline.process_item(
            session, config, http, _FakePlex(), [_NoArtProvider()],
            RenderIntent(kind="episode", title="Chapter One", season_number=1, episode_number=1),
            plex_generated_base=plex_generated_base,
        )

    title_card = next(r for r in results if r.art_kind == "title_card")
    assert title_card.status == "rendered"
    assert title_card.source_mode == "plex_generated"
    assert title_card.source_url == "plex://900/title_card"


async def test_title_card_refusal_is_contained_and_carries_no_token(session, caplog):
    """A Plex body that does not decode raises SourceRefused inside
    fetch_plex_generated_base -> render_artifact, uncaught there. An
    episode's only art kind is title_card (ART_KINDS_FOR["episode"]), so
    process_item's per-kind containment catches it, records the render row
    `failed`, and then -- every kind having refused -- re-raises SourceRefused
    itself, exactly as `test_every_kind_refusing_still_fails_the_job`
    (tests/test_pipeline_e2e.py) proves for a movie's two kinds both
    refusing. The token must appear nowhere: not in the raised exception, not
    in any log record, and not in the committed render row's detail."""
    import functools

    from autoposter.db.models import Render
    from autoposter.render.pipeline import SourceRefused, fetch_plex_generated_base

    class _FakeEntry:
        def __init__(self, rating_key, key):
            self.ratingKey = rating_key
            self.key = key

    class _FakePlexItem:
        def posters(self):
            return [
                _FakeEntry(
                    "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
                    "/library/metadata/900/file?url=media%3A%2F%2F5%2Fx.bundle...",
                ),
            ]

    class _FakePlex:
        async def resolve(self, intent):
            return ResolvedItem(
                rating_key="900", library="Severance (2022)", kind="episode",
                title="Chapter One", year=2022, season_number=1, episode_number=1,
                root_folder="Severance (2022)", file_path="/mnt/Media/x.mkv",
                art_url=None, tmdb_id=1, tvdb_id=None, imdb_id=None,
            )

        async def fetch_item(self, rating_key):
            return _FakePlexItem()

    class _NoArtProvider:
        name = "TMDB"

        async def fetch(self, request):
            return []

    async def bad_handler(request):
        return httpx.Response(200, content=b"not an image")

    config = load_config(EXAMPLE)
    config.badges.enabled = False

    async with httpx.AsyncClient(transport=httpx.MockTransport(bad_handler)) as http:
        plex_generated_base = functools.partial(
            fetch_plex_generated_base, http, _FakePlex(),
            base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
        )
        with caplog.at_level("WARNING"):
            with pytest.raises(SourceRefused) as excinfo:
                await pipeline.process_item(
                    session, config, http, _FakePlex(), [_NoArtProvider()],
                    RenderIntent(kind="episode", title="Chapter One", season_number=1, episode_number=1),
                    plex_generated_base=plex_generated_base,
                )

    raised_message = str(excinfo.value)
    assert "tok" not in raised_message
    assert "X-Plex-Token" not in raised_message

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "tok" not in log_text
    assert "X-Plex-Token" not in log_text

    # render_artifact's per-kind row is committed before process_item's
    # aggregate raise (pipeline.py's containment commits render.status =
    # "failed" first, then re-raises once every kind has refused), so it is
    # still there to read back on a fresh query.
    rows = (await session.execute(select(Render))).scalars().all()
    title_card = next(r for r in rows if r.art_kind == "title_card")
    assert title_card.status == "failed"
    assert "the title_card source did not decode after download" in (title_card.detail or "")
    assert "tok" not in (title_card.detail or "")
    assert "X-Plex-Token" not in (title_card.detail or "")
```

`load_config`, `EXAMPLE`, `ResolvedItem`, `RenderIntent`, `decodable_png` and
`httpx` are already imported at the top of `tests/test_pipeline_facts.py` or
available via `conftest`; `functools` is imported locally inside each new
test to avoid a top-of-file import this file did not previously need.

- [ ] **Step 20: Run `test_pipeline_facts.py` and confirm the new tests fail, the old ones still pass**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s20 test sh -c 'pytest -q tests/test_pipeline_facts.py 2>&1 | tee /app/.superpowers/run-t1-s20.log'
docker wait pf1-t1s20
docker cp pf1-t1s20:/app/.superpowers/run-t1-s20.log .superpowers/run-t1-s20.log
docker rm pf1-t1s20
tail -30 .superpowers/run-t1-s20.log
```

Expected: the two new tests fail (before this step they'd have failed with
`TypeError: process_item() got an unexpected keyword argument
'plex_generated_base'`; after Step 19's signature change they should now run
but may fail for other reasons if there's a bug — inspect the failure).
Every pre-existing test in the file must already pass at this point since
Step 19 also fixed the four fakes. If any pre-existing test still fails,
STOP and fix the fake signature before continuing.

Since Step 19 makes both the signature change and the test additions
together (they are inseparable — the fakes must be fixed in the same commit
that changes the call site, or the suite is red on `main` history), re-run
after confirming the two new tests are the only failures, then proceed
directly to Step 21.

- [ ] **Step 21: Confirm everything is green**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s21 test sh -c 'pytest -q tests/test_pipeline_facts.py 2>&1 | tee /app/.superpowers/run-t1-s21.log'
docker wait pf1-t1s21
docker cp pf1-t1s21:/app/.superpowers/run-t1-s21.log .superpowers/run-t1-s21.log
docker rm pf1-t1s21
tail -10 .superpowers/run-t1-s21.log
```

Expected: all of `test_pipeline_facts.py` passes.

```bash
git add src/autoposter/render/pipeline.py tests/test_pipeline_facts.py
git commit -m "feat(pipeline): thread plex_generated_base through process_item"
```

- [ ] **Step 22: Extend the `source_mode` comment in `db/models.py`**

In `src/autoposter/db/models.py`, find:

```python
    # 'generate' composites our own text and fade over textless art.
    # 'verbatim' applies supplied art untouched (the MediUX seam, spec section 11).
    source_mode: Mapped[str] = mapped_column(String(16), default="generate")
```

Replace with:

```python
    # 'generate' composites our own text and fade over textless art.
    # 'verbatim' applies supplied art untouched (the MediUX seam, spec section 11).
    # 'plex_generated' composites over the frame Plex itself derived from the
    # media file, when no provider had a title_card (roadmap row 239).
    source_mode: Mapped[str] = mapped_column(String(16), default="generate")
```

```bash
git add src/autoposter/db/models.py
git commit -m "docs(models): name plex_generated among source_mode's values"
```

- [ ] **Step 23: Write the failing flag test**

Add to `tests/test_action_flags.py`, immediately after
`test_show_fallback_fires_on_the_source_mode_row_two_seven_shipped`:

```python
async def test_plex_generated_fires_on_the_source_mode_row_and_is_silent_on_a_rendered_one(
    session, config
):
    """The plex-preview fallback (roadmap row 239) gets the same operator
    surface row 132's show_fallback already has."""
    flagged = await _seed(session, art_kind="title_card", source_mode="plex_generated")
    healthy = await _seed(session, art_kind="title_card", source_mode="generate")

    fired = await _fires_on(session, config, "plex_generated")

    assert flagged.id in fired
    assert healthy.id not in fired
```

Then update `test_every_flag_declares_a_label_a_description_and_a_detail`'s
set:

```python
    assert set(flags.FLAGS) == {
        "missing", "skipped", "truncated", "render_failed", "show_fallback",
        "upload_failed", "unknown_provenance", "language_miss", "provider_downgrade",
        "textless_miss", "logo_fallback", "unscored",
    }
```

to:

```python
    assert set(flags.FLAGS) == {
        "missing", "skipped", "truncated", "render_failed", "show_fallback",
        "plex_generated", "upload_failed", "unknown_provenance", "language_miss",
        "provider_downgrade", "textless_miss", "logo_fallback", "unscored",
    }
```

- [ ] **Step 24: Run and confirm both fail**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s24 test sh -c 'pytest -q tests/test_action_flags.py 2>&1 | tee /app/.superpowers/run-t1-s24.log'
docker wait pf1-t1s24
docker cp pf1-t1s24:/app/.superpowers/run-t1-s24.log .superpowers/run-t1-s24.log
docker rm pf1-t1s24
tail -20 .superpowers/run-t1-s24.log
```

Expected: `test_plex_generated_fires_on_the_source_mode_row_and_is_silent_on_a_rendered_one`
fails with `KeyError: 'plex_generated'`, and
`test_every_flag_declares_a_label_a_description_and_a_detail` fails on the
set comparison.

- [ ] **Step 25: Add the predicate and registry entry**

In `src/autoposter/actions/flags.py`, find:

```python
def _show_fallback(config: Config) -> ColumnElement[bool]:
    return Render.source_mode == "show_fallback"
```

Add immediately after it:

```python
def _plex_generated(config: Config) -> ColumnElement[bool]:
    return Render.source_mode == "plex_generated"
```

Then find the `show_fallback` `Flag(...)` entry in `_REGISTRY` and add a new
entry immediately after it:

```python
    Flag(
        code="show_fallback",
        label="Styled from the show poster",
        description=(
            "No provider had art for this season, so the show's own poster was "
            "styled with the season text instead."
        ),
        default_on=True,
        instant=True,
        predicate=_show_fallback,
        detail=_plain("season art came from the show's poster"),
    ),
    Flag(
        code="plex_generated",
        label="Plex's own preview frame",
        description=(
            "No provider had a title card for this episode, so the frame Plex "
            "generated from the media file was used as the base instead. On "
            "by default: every row this fires on already fires missing "
            "today, so the queue's population does not grow, only its "
            "labelling improves."
        ),
        default_on=True,
        instant=True,
        predicate=_plex_generated,
        detail=_plain("no title card on any provider; Plex's generated frame was used"),
    ),
```

(Only the new `Flag(...)` block is added — the `show_fallback` entry above it
is unchanged, shown here only to anchor where the new entry goes.)

- [ ] **Step 26: Run and confirm both pass**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s26 test sh -c 'pytest -q tests/test_action_flags.py 2>&1 | tee /app/.superpowers/run-t1-s26.log'
docker wait pf1-t1s26
docker cp pf1-t1s26:/app/.superpowers/run-t1-s26.log .superpowers/run-t1-s26.log
docker rm pf1-t1s26
tail -10 .superpowers/run-t1-s26.log
```

Expected: all of `test_action_flags.py` passes.

```bash
git add src/autoposter/actions/flags.py tests/test_action_flags.py
git commit -m "feat(action-center): flag plex_generated, default-on, instant"
```

- [ ] **Step 27: Write the failing app.py wiring tests**

Add to `tests/test_app.py` (near `test_handle_intent_passes_the_artwork_probe_through_to_process_item`
and `test_the_wired_artwork_probe_reads_provenance_for_one_plex_item`):

```python
async def test_handle_intent_passes_plex_generated_base_through_to_process_item():
    """The plex-preview rung (roadmap row 239) is only useful if it is
    actually wired: app.py builds the partial, _handle_intent has to carry
    it through to process_item exactly like artwork_probe does."""
    seen = {}

    async def capture(*args, **kwargs):
        seen.update(kwargs)

    plex_generated_base = object()
    config = load_config(EXAMPLE)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("autoposter.app.process_item", capture)
        await _handle_intent(
            None, RenderIntent(kind="movie", title="Dune", tmdb_id=1),
            config_holder=ConfigHolder(config), http=None, plex=None, providers=[],
            plex_generated_base=plex_generated_base,
        )

    assert seen["plex_generated_base"] is plex_generated_base


async def test_the_wired_plex_generated_base_fetches_the_generated_frame_for_one_item(
    tmp_path,
):
    """The shape app.py builds -- functools.partial(fetch_plex_generated_base,
    http, plex, base_url=..., headers=...) -- must be callable with just the
    rating key and a destination path, exactly as render_artifact calls it."""
    import functools

    from conftest import decodable_png

    from autoposter.render.pipeline import fetch_plex_generated_base

    class FakeEntry:
        def __init__(self, rating_key, key):
            self.ratingKey = rating_key
            self.key = key

    class FakePlexItem:
        def posters(self):
            return [
                FakeEntry("upload://abc", "/library/metadata/1/file?url=upload..."),
                FakeEntry(
                    "media://5/x.bundle/Contents/Thumbnails/thumb1.jpg",
                    "/library/metadata/1/file?url=media%3A%2F%2F5%2Fx.bundle...",
                ),
            ]

    class FakePlex:
        async def fetch_item(self, rating_key):
            assert rating_key == "153736"
            return FakePlexItem()

    async def handler(request):
        assert request.headers["X-Plex-Token"] == "tok"
        return httpx.Response(200, content=decodable_png())

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    plex_generated_base = functools.partial(
        fetch_plex_generated_base, http, FakePlex(),
        base_url="http://plex.local", headers={"X-Plex-Token": "tok"},
    )

    destination = tmp_path / "base.jpg"
    sha = await plex_generated_base("153736", destination, stage="the title_card source")

    assert sha is not None
    assert destination.exists()
    await http.aclose()
```

- [ ] **Step 28: Run and confirm both fail**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s28 test sh -c 'pytest -q tests/test_app.py -k plex_generated_base 2>&1 | tee /app/.superpowers/run-t1-s28.log'
docker wait pf1-t1s28
docker cp pf1-t1s28:/app/.superpowers/run-t1-s28.log .superpowers/run-t1-s28.log
docker rm pf1-t1s28
tail -30 .superpowers/run-t1-s28.log
```

Expected: the first fails with `TypeError: _handle_intent() got an
unexpected keyword argument 'plex_generated_base'`; the second passes
already (it calls `fetch_plex_generated_base` directly, which Step 13 already
shipped) — confirm this, and if it fails for an unrelated reason, fix it
before continuing.

- [ ] **Step 29: Wire the partial into `app.py`**

In `src/autoposter/app.py`, change the import line:

```python
from autoposter.render.pipeline import SourceRefused, process_item
```

to:

```python
from autoposter.render.pipeline import SourceRefused, fetch_plex_generated_base, process_item
```

Then find the `artwork_probe` partial:

```python
        artwork_probe = functools.partial(
            artwork_provenance, http,
            base_url=config.plex.url,
            headers={"X-Plex-Token": secrets.plex_token},
        )
```

Add immediately after it:

```python
        # The plex-preview fallback (roadmap row 239): when no provider has
        # a title_card, ask Plex for the frame it derived from the media
        # file itself (posters(), the media://-prefixed entry -- never our
        # own upload:// or an agent guess, see
        # plex/artwork.generated_title_card_url and the probe banked at
        # docs/research/2026-09-03-plex-episode-posters-probe.md). Built
        # here, once, so render_artifact never holds the token -- the same
        # shape as artwork_probe just above.
        plex_generated_base = functools.partial(
            fetch_plex_generated_base, http, app.state.plex,
            base_url=config.plex.url,
            headers={"X-Plex-Token": secrets.plex_token},
        )
```

Then find the `handler` partial:

```python
        handler = functools.partial(
            _handle_intent, config_holder=app.state.config_holder, http=http,
            plex=app.state.plex, providers=app.state.providers,
            tmdb_facts=app.state.tmdb_facts, mdblist=app.state.mdblist,
            artwork_probe=artwork_probe, imdb_parental=app.state.imdb_parental,
        )
```

Replace with:

```python
        handler = functools.partial(
            _handle_intent, config_holder=app.state.config_holder, http=http,
            plex=app.state.plex, providers=app.state.providers,
            tmdb_facts=app.state.tmdb_facts, mdblist=app.state.mdblist,
            artwork_probe=artwork_probe, imdb_parental=app.state.imdb_parental,
            plex_generated_base=plex_generated_base,
        )
```

Then find `_handle_intent`'s own signature and body:

```python
async def _handle_intent(
    session, intent, *, config_holder, http, plex, providers, tmdb_facts=None, mdblist=None,
    artwork_probe=None, imdb_parental=None,
):
    # Dereferenced once per job, at the top: process_item takes a config per
    # call already, so one read here is all it takes for a config swap to be
    # visible to the very next item a worker picks up. One read rather than
    # several also means a single job never straddles two generations.
    config = config_holder.current
    try:
        await process_item(
            session, config, http, plex, providers, intent,
            tmdb_facts=tmdb_facts, mdblist=mdblist, artwork_probe=artwork_probe,
            imdb_parental=imdb_parental,
        )
```

Replace with:

```python
async def _handle_intent(
    session, intent, *, config_holder, http, plex, providers, tmdb_facts=None, mdblist=None,
    artwork_probe=None, imdb_parental=None, plex_generated_base=None,
):
    # Dereferenced once per job, at the top: process_item takes a config per
    # call already, so one read here is all it takes for a config swap to be
    # visible to the very next item a worker picks up. One read rather than
    # several also means a single job never straddles two generations.
    config = config_holder.current
    try:
        await process_item(
            session, config, http, plex, providers, intent,
            tmdb_facts=tmdb_facts, mdblist=mdblist, artwork_probe=artwork_probe,
            imdb_parental=imdb_parental, plex_generated_base=plex_generated_base,
        )
```

- [ ] **Step 30: Run and confirm both pass**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1s30 test sh -c 'pytest -q tests/test_app.py 2>&1 | tee /app/.superpowers/run-t1-s30.log'
docker wait pf1-t1s30
docker cp pf1-t1s30:/app/.superpowers/run-t1-s30.log .superpowers/run-t1-s30.log
docker rm pf1-t1s30
tail -10 .superpowers/run-t1-s30.log
```

Expected: all of `test_app.py` passes.

```bash
git add src/autoposter/app.py tests/test_app.py
git commit -m "feat(app): wire plex_generated_base through the composition root"
```

- [ ] **Step 31: Run the full suite and compare against the baseline**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t1full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t1-full.log'
docker wait pf1-t1full
docker cp pf1-t1full:/app/.superpowers/run-t1-full.log .superpowers/run-t1-full.log
docker rm pf1-t1full
tail -10 .superpowers/run-t1-full.log
docker compose -p pf1 down
```

Expected: `BASELINE + 21` passing (`test_plex_artwork.py` [6] +
`test_pipeline.py` [10: 2 `_download` + 3 `fetch_plex_generated_base` + 5
rung] + `test_pipeline_facts.py` [2] + `test_action_flags.py` [1] +
`test_app.py` [2] = 21 — count and record the exact delta against Step 2's
`BASELINE` in the task report), zero skipped beyond baseline, zero failures.
If the count does not match, investigate before moving to Task 2 — do not
assume.

---

## Task 2: wrap

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`

**Interfaces:**
- Consumes: nothing new — this task only documents what Task 1 shipped.

- [ ] **Step 1: Add the two roadmap rows**

In `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, after the last
row (238), add two new rows to the same table:

```markdown
| 239 | Title-card fallback to Plex's own generated preview | User request (2026-09-03, screenshot of an episode showing "No provider offered artwork of this kind" next to a base image that came from adoption, not a live fallback): when no provider offers a title_card, use whatever preview frame Plex itself derived from the episode's media file. **A1 (whether `posters()` exposes that frame at all) was answered by a live read-only probe against the operator's server** (episode 153736, banked verbatim at `docs/research/2026-09-03-plex-episode-posters-probe.md`): the LAST `posters()` entry, `provider=None`, `ratingKey` prefixed `media://...`, fetched through the same PMS-authenticated path the badge stage already reads artwork from. **delivered:** the row-132 twin — `render_artifact`'s title_card branch asks Plex for that entry when the ladder comes back empty, downloads it through `_download` (now carrying a `headers` kwarg for `X-Plex-Token` and `follow_redirects=False`, buying #131's validation for free), and stores a STABLE synthetic `source_url` (`plex://<rating_key>/title_card`, never the live thumb URL, which carries a cache-busting epoch this service's own uploads bump every pass) so `base_sha256` alone decides re-render. Selection is by the `media://` prefix specifically, never `_agent_default`'s upload-vs-agent split — the self-feeding loop an episode's own previously-uploaded card would otherwise cause (an `upload://` entry can never match) is refused by construction, not by a provenance guard. `source_mode = 'plex_generated'`, cleared back to `'generate'` symmetrically with row 132's own `show_fallback` clear; flag `plex_generated`, instant, default-on (every row it fires on already fires `missing` today, so the queue's size does not change). Row 47 (`disable_online_asset_fetch`) makes the rung unreachable there, same as row 11 documented for the whole empty-ladder case — left as-is, an asymmetry now named in the rung's own comment rather than restructured | M — render path, one new plex/artwork.py helper, one flag | parity-plus | 132; 11; 131 |
| 240 | Superseded `upload://` posters accumulate in Plex (FILED) | The same probe that answered row 239's A1 also surfaced five `upload://` entries on one episode already re-rendered a handful of times, one `selected=True` and four orphaned — every successful `lockPoster` adds a new entry rather than replacing the last one, and nothing in this service ever removes an old upload. Not built as part of row 239: cleanup needs a way to tell "ours, superseded" apart from "ours, current" and from every other `upload://` entry a different tool or operator might have made — a candidate is the EXIF provenance stamp this service already writes (`_already_in_plex`, `pipeline.py`), but that is not yet designed against what Plex's API actually lets a caller delete versus merely unselect | S–M — needs its own design pass over what Plex exposes for removing a superseded upload | parity-plus | 239 |
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit -m "docs(roadmap): row 239 closes, row 240 files the upload:// cleanup"
```

- [ ] **Step 3: Final full-suite run and scope check**

```bash
docker compose -p pf1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pf1-t2full test sh -c 'pytest -q 2>&1 | tee /app/.superpowers/run-t2-full.log'
docker wait pf1-t2full
docker cp pf1-t2full:/app/.superpowers/run-t2-full.log .superpowers/run-t2-full.log
docker rm pf1-t2full
tail -10 .superpowers/run-t2-full.log
docker compose -p pf1 down
```

Expected: identical pass count to Task 1's Step 31. Then confirm the diff's
scope:

```bash
git diff --stat origin/main -- src/autoposter/ tests/ docs/
```

Expected: only the files listed in the File Structure table above.

- [ ] **Step 4: Prepare the PR body**

```bash
git log origin/main..HEAD --oneline
```

Use this as the commit list for a PR body along these lines (fill in the
measured counts from Step 31 / Task 2 Step 3):

```markdown
## Summary
- When no provider offers a title_card for an episode, fall back to the
  preview frame Plex itself derived from the media file — the row-132
  (season-poster→show-poster) twin for episodes.
- Selection is the `media://`-prefixed `posters()` entry specifically
  (confirmed by a live probe, banked at
  docs/research/2026-09-03-plex-episode-posters-probe.md) — never the first
  non-upload:// entry, which is Plex's own agent guess, and never an
  upload:// entry, which sidesteps the self-feeding-loop risk by
  construction rather than a provenance guard.
- `source_url` is a stable synthetic key (`plex://<rating_key>/title_card`),
  never the live, cache-busted Plex thumb URL, so a bumped epoch never
  re-renders the row and only changed bytes do.
- New Action Center flag `plex_generated`, instant, default-on — every row
  it fires on already fired `missing` today, so the queue's population is
  unchanged, only its labelling improves.
- Roadmap row 239 closes; row 240 files the pre-existing `upload://`
  accumulation this row's probe surfaced, out of scope here.

## Test plan
- [ ] `pytest -q` — <BASELINE> + 21, zero new failures
- [ ] `tests/test_plex_artwork.py` — the media:// selection rule, unit-level
- [ ] `tests/test_pipeline.py` — gate-off byte-identical, gate-on first pass,
      no-art-either-way, later-provider-art clears the fallback, epoch
      stability
- [ ] `tests/test_pipeline_facts.py` — the self-feed pin and the download
      refusal path, both through `process_item`
- [ ] `tests/test_action_flags.py` — the new flag's fires/silent pin
- [ ] `tests/test_app.py` — the composition-root wiring
```

This is not sent anywhere automatically — hand it to `gh pr create --body`
when the branch is ready to open a PR, per the repository's own PR
conventions.
