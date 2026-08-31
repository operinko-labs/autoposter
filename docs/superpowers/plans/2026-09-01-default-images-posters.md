# Default-Images Posters — Collection Art for Every Family We Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every managed collection family this service actually builds — and for which `Kometa-Team/Default-Images` holds per-member art — its upstream default poster, keyed by the family's own natural key, cached on disk, falling back silently to today's behaviour (the operator's local file, or nothing) wherever no asset matches.

**Architecture:** One new pure-plus-cache module, `collections/default_images.py`, owns the whole mapping: family → (upstream directory, key scheme) plus the three code tables the display-name families cannot derive (universe short codes, streaming provider ids, franchise decoration-strip variants). `apply_poster` gains ONE branch — a `kind` that names a default-image family resolves through that module to a cached file on disk, below the operator's local override and beside the separator-art `generated=` file that already occupies that rank — so `hosted_poster_url`'s six-kind table is not touched and the local-override priority is structurally unchanged. Reaching that branch is three separate wirings: `TmdbCollectionBuilder` (franchises, a list builder that already flows to `apply_poster`), a new `DynamicType.poster_kind` column threaded through `reconcile_smart_collection`'s hardcoded `None, None` (nine dynamic families at once), and three list builders whose family identity lives in their params rather than their name (`tmdb_discover`'s watch providers, `plex_all`'s resolution filter, the three universe list builders' list refs).

**Tech Stack:** Python 3.13/3.14, httpx (already a dependency; `MockTransport` for every fetch test), Pillow (already a dependency, `_is_image`), pytest in the compose test container. No new dependency, no new config key, no new UI.

**Binding requirements:** `.superpowers/sdd/p-defimg-facts.md` (C1–C4, settled). Where this plan and that file could ever disagree, that file wins. Evidence base: `.superpowers/sdd/p-defimg-recon.md` (the pipeline) and `.superpowers/sdd/p-defimg-probe.md` (the upstream inventory).

## Global Constraints

Every task's requirements implicitly include this section.

1. **No live GitHub in tests.** Every fetch test drives `httpx.MockTransport`. Not one test in this phase may reach `raw.githubusercontent.com` or `api.github.com`. The one live read in the phase is T1's supplementary listing probe, which is a manual capture, not a test.
2. **Only the BASE full-poster asset is consumed.** `logos/`, `overlays/`, `white/`, `best/`, `standards/` are never fetched. For the flat families that is `<dir>/<name>.jpg`; for the four Pattern-B families the probe names (`chart`, `network`, `country`, `streaming`), whose base directory holds nothing but an index `.webp`, the base full poster IS `<dir>/color/<name>.jpg` — which is exactly the path the already-shipped `chart` kind uses (`posters.hosted_poster_url`, `chart/color/{key}.jpg`). Those are the only two shapes; a family row spells its directory in full and no code derives one from the other.
3. **The existing switch governs.** `config.collections.posters` (default `True`) is the one on/off, unchanged. NO new config key, NO new toggle, NO new UI. `posters_enabled` already gates every `apply_poster` call site.
4. **Local override always wins.** The operator's `<assets_root>/<library>/<title>/poster.<ext>` (or the flat layout) is read first, unconditionally, before `kind`/`key` is consulted. Nothing in this phase may reorder that, and T1 pins it with a test.
5. **A miss is silent.** No upstream asset for a key is one `logger.debug` line and today's behaviour — never a WARNING, never a raise, never a partial write. "Where available" is the spec.
6. **Branch (facts C4).** `feat/default-images-posters`, cut from `origin/main` after `git fetch origin`, verified with a **content probe** (never a sha probe) — see T1 Step 1.
7. **Container discipline (the standing recipe).** Unique compose project per task — `pdi1`, `pdi2`, `pdi3`, `pdi4` — always with the `.superpowers/isolated-db.yml` overlay. Tee output to a path under `/app/.superpowers/` inside the container; never rely on streamed stdout (it is filtered, and `--rm` deletes the container before it can be re-read). A long run (the full suite) uses **no `--rm`**, is started detached with `run -d --name <project>-full`, and is waited on with a foreground `docker wait`; the log is then read from the host. Teardown is `docker compose -p <project> down` — **never** `down -v`. Two pytest commands against the same compose project are unsafe — keep the `-p` names unique.
8. **Commits.** Conventional messages, `--no-gpg-sign`, staged **by name** (never `git add -A`). No `Co-Authored-By`, no AI attribution of any kind, in commits or the PR body.
9. **Citations.** Test docstrings cite `p-defimg-probe.md` **by section name** ("§1 Franchise posters", "§5 `universe/`"), never by line number — the by-line citation guard does not cover that file and a line cite there rots invisibly.
10. **Artifacts.** Phase artifacts under `.superpowers/sdd/` use the `p-defimg-` prefix. The PR body goes to `.superpowers/sdd/p-defimg-pr-body.md`, gitignored and uncommitted. **Push and PR creation are NOT in this plan** — the controller does both after the whole-branch review.
11. **Suites stated-then-measured.** Every task states its expected test delta against T1's MEASURED baseline and then measures it. A stated number that does not match the measured one is investigated, never overwritten.
12. **Golden gate byte-identical.** `tests/fixtures/collections/golden_port.json` must not change in any task — assert it with a real run in every task that touches `src/`, never by assuming it.
13. **Every refusal names the way out.** A message that says only what is wrong is half a message.
14. **Licence posture, recorded not re-litigated.** The module docstring records the maintainer's derivative-works answer verbatim and points at the two existing records (`collections/posters.py`'s module docstring and `assets/fonts/PROVENANCE.md`). No new licence claim is made and no image is vendored.

---

## Adjudications this plan makes beyond the facts file

Each is flagged, and each is overridable at the gate.

- **A1 — the `generated=` rank, not a new `hosted_poster_url` kind.** Facts C1.3 requires the fetch to cache under `<assets_root>/.generated/collection-posters/<family>/…`, the separator-art precedent. `hosted_poster_url` is a *pure* function that never caches and never fetches, so the cached path cannot come from it. The default image is therefore resolved to a FILE and consumed by the same branch `separator_art` already feeds (`apply_poster`'s middle rank), with the source label parameterised so the report says which source it was. `hosted_poster_url` is not touched at all. This keeps the local-override priority (Constraint 4) structurally true rather than re-argued.
- **A2 — six of the sixteen in-scope families are OUT because we do not build them.** Facts C1.1 scopes to families "WHERE we currently build that family". Verified against `collections/catalog.py`: `aspect` (`media_aspect` is GATED, no collections), `year` (`time_year` is GATED), `seasonal` (`time_seasonal` is GATED) and `video_format` (no catalog row at any spelling) build nothing today, so they are OUT of the packs. `year` and `resolution` nevertheless ship as `DynamicType` rows an operator can write by hand, so their poster columns ARE filled in T2 — free, and the pack flipping READY later costs nothing.
- **A3 — franchise posters are keyed by the collection's own title, not by TMDb id.** The probe found no id-based naming anywhere upstream. `TmdbCollectionBuilder` has `ctx.definition.title` in scope (`engine.py:389` sets `definition=definition` for every builder, not only family builders), which for a `facts_family` unit IS TMDb's own collection name with the pack's `remove_suffix: [" Collection"]` applied and any `title_override` honoured. Calling `client.collection_name(params.id)` a second time was rejected: it is one extra GET per franchise on a cache-less deployment, up to 250 per pass.
- **A4 — three list-built families have no builder-specific seam, so their key is read off the params/filters that give them their identity.** `production_streaming` is `tmdb_discover`, `media_resolution` is `plex_all`, `content_universes`/`content_dc` are `imdb_list`/`tmdb_list`/`mdblist_list` — all generic builders. Each reads the one field that names the family (`with_watch_providers`, the definition's `resolution` filter, the list ref) and asks the mapping module; a definition of the same builder that carries no such field gets `None` and behaves exactly as today. No `CollectionDefinition` field is added — that would be operator-facing config surface the facts do not authorise.
- **A5 — misses are cached as markers.** The display-name families try the exact key first and then the documented strip-variants, so a miss would otherwise cost up to four GETs per collection per pass, and franchise misses are the COMMON case (upstream curates 116 franchises; our family enumerates whatever the library holds). A miss writes an empty `<key>.miss` marker beside where the hit would have gone. Consequence, stated: newly-added upstream art is picked up on cache loss, not on the next pass. The cache is `.generated/`, already prune-exempt (`scheduler/jobs.py:519`), and deleting it is the documented way to re-check.
- **A6 — the leftovers bucket is never mapped.** `dynamic_titles.OTHER_KEY` is `"other"`, and `aspect/other.jpg` exists upstream — so an unguarded lookup would give "Other Countries" the aspect-ratio catch-all's artwork if the families were ever merged, and would in any case ask for `country/color/other.jpg`. Every dynamic wiring skips `OTHER_KEY` explicitly.
- **A7 — `content_rating`'s dynamic row gets no poster column.** Upstream's content-rating art is region-scoped (`content_rating/<region>/<rating>.jpg`) and the `cs_bucket` family is already wired to the `cs` region by hand (`reconcile.py:1146`). A `type: content_rating` dynamic definition names no region, and picking one for the operator would be a guess that resolves to plausible wrong artwork. Left `None`, with the reason in the row's note.
- **A8 — four upstream keys are UNCONFIRMED and T1 probes them before the table is written.** `p-defimg-probe.md` §5 sampled 9 of `universe/`'s 22 entries and never sampled `streaming/color/` or `country/color/` at all. The codes for Star Trek, Star Wars, X-Men and the DCEU, and the exact `streaming/color/` and `country/color/` filenames, are therefore fetched live in T1 Step 2 and appended to the probe file as a new section. **No code in this phase is written from a recalled or guessed upstream filename.**

---

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `src/autoposter/collections/default_images.py` | **Create.** The whole mapping layer: `Family` rows (directory + key scheme), `UNIVERSE_CODES`, `STREAMING_NAMES`, `RESOLUTION_KEYS`, the name-variant ladder, `default_image_url`, and the cache-then-fetch `ensure_default_image`. One module, one responsibility: "which upstream file, if any, is this collection's". | T1 |
| `src/autoposter/collections/posters.py` | **Modify.** `apply_poster` gains the default-image branch and a source label; module docstring gains the licence pointer. `hosted_poster_url` untouched. | T1 |
| `src/autoposter/collections/builders/tmdb.py` | **Modify.** `TmdbCollectionBuilder.build` sets `poster_kind="franchise"`, `poster_key=ctx.definition.title`. | T1 |
| `src/autoposter/collections/dynamic_types.py` | **Modify.** `DynamicType` gains `poster_kind: str \| None = None`; nine of the ten rows fill it. | T2 |
| `src/autoposter/collections/smart.py` | **Modify.** `reconcile_smart_collection` gains `poster_kind`/`poster_key` params and threads them into its `apply_poster` call, replacing the hardcoded `None, None`. | T2 |
| `src/autoposter/collections/builders/dynamic.py` | **Modify.** The per-unit loop passes `row.poster_kind` and `unit.key`, skipping `OTHER_KEY`. | T2 |
| `src/autoposter/collections/builders/tmdb_discover.py` | **Modify.** `TmdbDiscoverBuilder.build` maps `with_watch_providers` to a streaming poster. | T3 |
| `src/autoposter/collections/builders/plex_trivial.py` | **Modify.** `PlexAllBuilder.build` maps the definition's `resolution` filter to a resolution poster. | T3 |
| `src/autoposter/collections/builders/imdb_lists.py` | **Modify.** `ImdbListBuilder.build` maps its list id to a universe poster. | T3 |
| `src/autoposter/collections/builders/mdblist.py` | **Modify.** `MdblistListBuilder.build` maps its list ref to a universe poster. | T3 |
| `tests/test_collection_default_images.py` | **Create.** The mapping table's law: one key-scheme sample per family from the probe, the variant ladder, the URL encoding, the cache/miss-marker behaviour, the 404 fallback. | T1 |
| `tests/test_collection_poster_apply.py` | **Modify.** The default-image branch: priority (local > default image > none), the 404 fallback, the source label. | T1 |
| `tests/test_collection_poster_wiring.py` | **Modify.** Franchise wiring end to end (T1); the dynamic wiring end to end (T2); the three list wirings (T3). | T1/T2/T3 |
| `tests/test_collection_dynamic_types.py` | **Modify.** The poster column's table law: which rows carry one, which do not and why. | T2 |
| `.superpowers/sdd/p-defimg-probe.md` | **Modify (gitignored).** New `§6 Supplementary listing capture` with T1's live listings. | T1 |
| `.superpowers/sdd/p-defimg-probe.py` | **Create then delete (gitignored, transient).** The listing probe; preserved verbatim inside §6 so it is reproducible. | T1 |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | **Modify.** Row 220 filed and closed by this phase. | T4 |
| `.superpowers/sdd/p-defimg-pr-body.md` | **Create (gitignored, uncommitted).** The PR body for the controller. | T4 |

---

### Task 1: The mapping module, the fetch/cache, and franchises

**Files:**
- Create: `src/autoposter/collections/default_images.py`
- Create: `tests/test_collection_default_images.py`
- Modify: `src/autoposter/collections/posters.py`
- Modify: `src/autoposter/collections/builders/tmdb.py`
- Modify: `tests/test_collection_poster_apply.py`
- Modify: `tests/test_collection_poster_wiring.py`
- Modify (gitignored): `.superpowers/sdd/p-defimg-probe.md`
- Commit: `docs/superpowers/plans/2026-09-01-default-images-posters.md` (this plan)

**Interfaces:**
- Produces, and Tasks 2 and 3 consume by these exact names:
  - `default_images.FAMILIES: dict[str, Family]` — the kind vocabulary. A `kind` in this dict is a default-image family; anything else falls through to `hosted_poster_url` unchanged.
  - `default_images.default_image_url(family: str, key: str) -> str | None` — pure, no I/O; the FIRST candidate URL only.
  - `default_images.candidate_urls(family: str, key: str) -> tuple[str, ...]` — pure; every candidate in order (exact, then the strip variants).
  - `default_images.ensure_default_image(config, http, family: str, key: str) -> Path | None` — cache hit, else one validated fetch per candidate, else a miss marker and `None`.
  - `default_images.UNIVERSE_CODES: dict[str, str]` — list ref → upstream short code (T3).
  - `default_images.STREAMING_NAMES: dict[str, str]` — TMDb watch-provider id → upstream service name (T3).
  - `default_images.RESOLUTION_KEYS: frozenset[str]` — the resolution filter values that name an upstream asset (T3).
  - `posters.apply_poster(..., kind, key, ...)` — unchanged signature; a `kind` in `FAMILIES` now resolves through the module.

- [ ] **Step 1: Cut the branch and commit this plan**

```bash
git fetch origin
git checkout -b feat/default-images-posters origin/main
# Content probe (facts C4: the base must be post-#116/#117). Both must print OK.
grep -q 'ensure_separator_art' src/autoposter/collections/separator_art.py && echo CONTENT-PROBE-1-OK
grep -q 'decode: Callable' src/autoposter/providers/fetch.py && echo CONTENT-PROBE-2-OK
git add docs/superpowers/plans/2026-09-01-default-images-posters.md
git commit --no-gpg-sign -m "docs(plan): default-images posters for every family we build"
```

Expected: `CONTENT-PROBE-1-OK` and `CONTENT-PROBE-2-OK`, then one commit. If either probe fails, STOP and report — the base is wrong (the separator-art module and the decoder-parameterised provider fetch must both already be present).

- [ ] **Step 2: Capture the four unconfirmed upstream listings (the phase's only live read)**

Write `.superpowers/sdd/p-defimg-probe.py` exactly:

```python
"""List the four Default-Images directories p-defimg-probe.md left unsampled.

Read-only, public, unauthenticated. Prints verbatim filenames; nothing here
is summarised, and nothing downstream may use a filename this script did not
print. Runs in the pdi1 test container (httpx is installed there).
"""
import httpx

PATHS = (
    "universe",
    "streaming/color",
    "country/color",
    "network/color",
)
BASE = "https://api.github.com/repos/Kometa-Team/Default-Images/contents/"

for path in PATHS:
    response = httpx.get(BASE + path, timeout=30,
                         headers={"Accept": "application/vnd.github+json"})
    print("=== %s HTTP %d" % (path, response.status_code))
    if response.status_code != 200:
        continue
    entries = response.json()
    print("count %d" % len(entries))
    for entry in entries:
        print("%s\t%s" % (entry["type"], entry["name"]))
```

- [ ] **Step 3: Run the probe and record the baseline in one container pass**

```bash
docker compose -p pdi1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "python /app/.superpowers/sdd/p-defimg-probe.py 2>&1 | tee /app/.superpowers/run-pdi1-probe.log"
docker compose -p pdi1 down

docker compose -p pdi1b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pdi1b-base test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pdi1-baseline.log"
docker wait pdi1b-base
tail -5 .superpowers/run-pdi1-baseline.log
docker compose -p pdi1b down
```

Expected from the probe log: four `HTTP 200` blocks. Read the `universe` block for the codes of Star Trek, Star Wars, X-Men and the DC Extended Universe; read `streaming/color` and `country/color` for their exact filenames. **If any block is not 200, STOP** — the affected family's rows are written as `# UNCONFIRMED — not shipped` comments rather than guessed, and the task reports the gap.

Expected from the baseline log: a `N passed` line. **Write that number down — it is this phase's baseline** (the facts expect ~4269 tests / ~362 files post-#116/#117; the MEASURED number governs, not that estimate). Every later "expected delta" in this plan is against it.

- [ ] **Step 4: Append §6 to the probe file**

Append to `.superpowers/sdd/p-defimg-probe.md`:

```markdown
## 6. Supplementary listing capture (2026-09-01, the posters phase T1)

The four directories §5 left unsampled or sampled partially, listed live via the
same GitHub Contents API §0 names. The probe script is preserved verbatim below
so the capture is reproducible; the run's raw output is
`.superpowers/run-pdi1-probe.log`.

### `universe/` — full listing (22 entries)

<paste the verbatim `type\tname` lines from the log's `universe` block>

### `streaming/color/` — full listing

<paste verbatim>

### `country/color/` — full listing

<paste verbatim>

### `network/color/` — full listing

<paste verbatim>

### Probe script

<paste `.superpowers/sdd/p-defimg-probe.py` verbatim>
```

Then delete the transient script:

```bash
rm .superpowers/sdd/p-defimg-probe.py
```

`.superpowers/` is gitignored — nothing here is committed. Section names, not line numbers, are what the tests will cite (Constraint 9).

- [ ] **Step 5: Write the failing test for the mapping table**

Create `tests/test_collection_default_images.py`:

```python
"""Which upstream file, if any, is a collection's default poster.

The table this pins is DATA, transcribed from the live listings recorded in
``.superpowers/sdd/p-defimg-probe.md`` -- one sample per key scheme, cited by
that file's own section names. Nothing here reaches the network: the fetch
tests drive ``httpx.MockTransport``.
"""
import httpx
import pytest

from autoposter.collections import default_images
from autoposter.collections.default_images import (
    FAMILIES,
    candidate_urls,
    default_image_url,
    ensure_default_image,
)

BASE = "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"


def test_the_flat_display_name_families_key_on_the_name_verbatim():
    """p-defimg-probe.md §1 (`franchise/Jurassic Park.jpg`), §5 `genre/`
    (`Action.jpg`) and §5 `studio/` (`101 Studios.jpg`): the exact display
    name is the filename, spaces and punctuation kept literal."""
    assert default_image_url("franchise", "Jurassic Park") == (
        BASE + "/franchise/Jurassic%20Park.jpg"
    )
    assert default_image_url("genre", "Action") == BASE + "/genre/Action.jpg"
    assert default_image_url("studio", "101 Studios") == (
        BASE + "/studio/101%20Studios.jpg"
    )


def test_the_pattern_b_families_read_the_color_rendering_not_the_bare_dir():
    """p-defimg-probe.md §4 Pattern B: `network`, `country` and `streaming`
    keep nothing at their base but an index image -- the full poster is the
    `color/` rendering, which is the path the shipped `chart` kind already
    uses."""
    assert default_image_url("network", "A&E") == BASE + "/network/color/A%26E.jpg"
    assert default_image_url("country", "Australia and New Zealand") == (
        BASE + "/country/color/Australia%20and%20New%20Zealand.jpg"
    )
    assert default_image_url("streaming", "Netflix") == (
        BASE + "/streaming/color/Netflix.jpg"
    )


def test_the_code_keyed_families_key_on_lowercase_codes_and_numbers():
    """p-defimg-probe.md §2/§3 (ISO codes, lowercase), §5 `decade/`
    (decade number), §5 `resolution/` (`4k.jpg`), §5 `year/`, §5 `universe/`
    (short lowercase code)."""
    assert default_image_url("audio_language", "fi") == BASE + "/audio_language/fi.jpg"
    assert default_image_url("subtitle_language", "fil") == (
        BASE + "/subtitle_language/fil.jpg"
    )
    assert default_image_url("decade", "1980") == BASE + "/decade/1980.jpg"
    assert default_image_url("resolution", "4k") == BASE + "/resolution/4k.jpg"
    assert default_image_url("year", "1999") == BASE + "/year/1999.jpg"
    assert default_image_url("universe", "mcu") == BASE + "/universe/mcu.jpg"


def test_an_unknown_family_or_an_empty_key_has_no_url():
    """The `hosted_poster_url` rule, kept: an unrecognised family returns None
    rather than guessing, because a wrong URL 404s quietly."""
    assert default_image_url("playlist", "Arrowverse") is None
    assert default_image_url("franchise", "") is None
    assert candidate_urls("playlist", "Arrowverse") == ()


def test_the_franchise_variant_ladder_tries_the_exact_name_first():
    """Our franchise titles are TMDb's, with the pack's ' Collection' suffix
    already stripped; upstream's are Kometa's own bucket names, and
    p-defimg-probe.md §1 shows three shapes ours can differ by --
    `Mission Impossible.jpg` for TMDb's 'Mission: Impossible',
    `Alien Predator.jpg` for our 'Alien / Predator', and a bare
    `Alien.jpg`/`Batman.jpg` for names ours may decorate. Exact first: a
    variant must never shadow a real hit."""
    urls = candidate_urls("franchise", "Mission: Impossible")
    assert urls[0] == BASE + "/franchise/Mission%3A%20Impossible.jpg"
    assert BASE + "/franchise/Mission%20Impossible.jpg" in urls

    slashed = candidate_urls("franchise", "Alien / Predator")
    assert slashed[0] == BASE + "/franchise/Alien%20%2F%20Predator.jpg"
    assert BASE + "/franchise/Alien%20Predator.jpg" in slashed

    decorated = candidate_urls("franchise", "Batman Collection")
    assert BASE + "/franchise/Batman.jpg" in decorated


def test_the_code_keyed_families_get_no_variant_ladder():
    """A code is a code: 'fi-FI' is not 'fi' by any documented rule upstream
    publishes, and inventing one here would fetch a plausible wrong flag."""
    assert candidate_urls("audio_language", "fi-FI") == (
        BASE + "/audio_language/fi-FI.jpg",
    )
    assert candidate_urls("decade", "1980") == (BASE + "/decade/1980.jpg",)


async def test_a_hit_is_written_to_the_generated_cache_and_reused(
    tmp_path, config_factory, jpeg_bytes
):
    """The separator-art precedent: `<assets_root>/.generated/` -- deliberately
    NOT the operator-override layout, so a fetched default can never masquerade
    as a hand-placed file."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=jpeg_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await ensure_default_image(config, http, "genre", "Action")
        second = await ensure_default_image(config, http, "genre", "Action")

    assert first == tmp_path / ".generated" / "collection-posters" / "genre" / "Action.jpg"
    assert first.read_bytes() == jpeg_bytes
    assert second == first
    assert calls == [BASE + "/genre/Action.jpg"]


async def test_a_404_leaves_no_file_returns_none_and_is_not_refetched(
    tmp_path, config_factory
):
    """The fallback that makes 'where available' honest: a family with no
    matching asset renders exactly today's behaviour, and the miss marker stops
    the variant ladder being re-walked on every pass."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await ensure_default_image(config, http, "genre", "Nope") is None
        before = len(calls)
        assert await ensure_default_image(config, http, "genre", "Nope") is None

    assert len(calls) == before
    root = tmp_path / ".generated" / "collection-posters" / "genre"
    assert not (root / "Nope.jpg").exists()
    assert (root / "Nope.miss").is_file()


async def test_a_variant_hit_is_cached_under_our_own_key(
    tmp_path, config_factory, jpeg_bytes
):
    """The cache path is derived from OUR key, not from the upstream name that
    answered -- so the ladder is walked once per collection, ever."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)

    async def handler(request):
        if request.url.path.endswith("/franchise/Alien%20Predator.jpg"):
            return httpx.Response(200, content=jpeg_bytes)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await ensure_default_image(
            config, http, "franchise", "Alien / Predator"
        )

    assert found == (
        tmp_path / ".generated" / "collection-posters" / "franchise"
        / "Alien%20%2F%20Predator.jpg"
    )
    assert found.read_bytes() == jpeg_bytes


async def test_a_key_with_a_separator_cannot_escape_the_cache_directory(
    tmp_path, config_factory, jpeg_bytes
):
    """A collection title is not a value this service chose. The cache stem is
    percent-encoded with nothing safe, so no key can add a path segment."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)

    async def handler(request):
        return httpx.Response(200, content=jpeg_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await ensure_default_image(config, http, "genre", "../../etc")

    root = tmp_path / ".generated" / "collection-posters" / "genre"
    assert found is not None
    assert found.parent == root


async def test_a_body_that_is_not_an_image_is_refused_and_leaves_no_file(
    tmp_path, config_factory
):
    """A 200 is not proof of an image -- upstream can answer 200 with HTML --
    and an unusable body cached would be uploaded, hashed, and never retried."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)

    async def handler(request):
        return httpx.Response(200, content=b"<html>not an image</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await ensure_default_image(config, http, "genre", "Action") is None

    root = tmp_path / ".generated" / "collection-posters" / "genre"
    assert not (root / "Action.jpg").exists()


async def test_no_http_client_refuses_the_fetch_but_not_the_cache(
    tmp_path, config_factory, jpeg_bytes
):
    """``separator_art._base_layer``'s posture: ``http`` may be None, which
    refuses the FETCH only."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    root = tmp_path / ".generated" / "collection-posters" / "genre"
    root.mkdir(parents=True)
    (root / "Action.jpg").write_bytes(jpeg_bytes)

    assert await ensure_default_image(config, None, "genre", "Nope") is None
    assert await ensure_default_image(config, None, "genre", "Action") == (
        root / "Action.jpg"
    )


def test_every_family_row_names_a_real_directory_shape():
    """The table's own integrity: a directory is either flat (`genre`) or one
    level deep for a Pattern B family (`network/color`), never deeper and never
    a logos/overlays/white/best/standards path -- those are overlay-phase
    material and are out of scope by C1.6."""
    for name, family in FAMILIES.items():
        assert family.directory.count("/") <= 1, name
        for banned in ("logos", "overlays", "white", "best", "standards"):
            assert banned not in family.directory.split("/"), name
```

Add the shared `jpeg_bytes` fixture to `tests/conftest.py` if it is not already there:

```python
@pytest.fixture
def jpeg_bytes():
    """A real 4x4 JPEG, so ``_is_image`` is exercised against real bytes rather
    than mocked."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="JPEG")
    return buffer.getvalue()
```

- [ ] **Step 6: Run the new test file and verify it fails**

```bash
docker compose -p pdi1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_default_images.py 2>&1 | tee /app/.superpowers/run-pdi1-red.log"
docker compose -p pdi1 down
tail -20 .superpowers/run-pdi1-red.log
```

Expected: collection error — `ModuleNotFoundError: No module named 'autoposter.collections.default_images'`. RED confirmed before a line of implementation.

- [ ] **Step 7: Write the mapping module**

Create `src/autoposter/collections/default_images.py`:

```python
"""Which upstream default poster, if any, a managed collection carries.

``collections/posters.py`` answers that question for the six kinds this
service has always known -- awards, charts, content ratings, separators -- from
a hand-checked table of Kometa's own folder names. This module answers it for
the families whose members are enumerated rather than curated: one row per
family we BUILD, naming the ``Kometa-Team/Default-Images`` directory its art
lives in and the key scheme that directory is named by.

**The table is DATA, and the data was listed rather than recalled.**
``.superpowers/sdd/p-defimg-probe.md`` records the live listings -- §1 through
§5 from the phase's first probe, §6 from this task's supplementary capture --
and every directory, every key scheme and every code in the three lookup
tables below is transcribed from those listings. Upstream keys by *name*, never
by id: the probe found no TMDb-collection-id naming anywhere in the repository,
so a franchise is ``franchise/Jurassic Park.jpg`` and not
``franchise/1241.jpg``.

**Four key schemes, and the split is upstream's, not ours.**

- *exact display name* -- ``franchise``, ``genre``, ``studio``, ``country``,
  ``network``, ``streaming``. Spaces and punctuation are literal in the
  filename and percent-encoded in the URL.
- *lowercase ISO code* -- ``audio_language``, ``subtitle_language``.
- *number* -- ``decade``, ``year``.
- *short lowercase code* -- ``universe`` (``mcu``, ``dcu``, ``dca`` ...), and
  ``resolution``'s labels (``4k``, ``1080``), which are neither names nor
  numbers.

**Two directory shapes, and no code derives one from the other.** Most families
keep their full posters flat at ``<dir>/<name>.jpg``. Four -- ``network``,
``country``, ``streaming`` and the already-shipped ``chart`` -- keep nothing at
their base but an index image, with the full poster under ``<dir>/color/``
(probe §4, "Pattern B"). Each row spells its directory in full for that reason.
``logos/``, ``overlays/``, ``white/``, ``best/`` and ``standards/`` are never
read: they are logo cutouts, name-stamped overlays and rendering variants, out
of scope by the phase's own C1.6, and the probe measured that their casing and
membership do not even match the base set.

**A miss is the common case, and it is silent.** Upstream curates 116
franchises; our franchise family enumerates whatever the library holds. So
``ensure_default_image`` answers ``None`` on a 404, logs one DEBUG line, and the
collection keeps exactly the poster it had -- the operator's own file, or
nothing. Never a WARNING: "where available" is the specification, not a
degraded state.

**Fetching and caching, the ``separator_art`` precedent.** A resolved image is
written under ``<assets_root>/.generated/collection-posters/<family>/`` --
deliberately NOT the ``<library>/<title>/poster.jpg`` operator-override layout,
so a fetched default can never masquerade as a hand-placed file and clobbering
one is structurally impossible. ``.generated`` is already exempt from the asset
prune (``scheduler/jobs.py``). The cache stem is OUR key, percent-encoded with
nothing safe -- so no collection title can add a path segment, and a name
variant that answered is not re-walked on the next pass. A MISS is cached too,
as an empty ``.miss`` marker: the display-name families try several candidate
names, and without the marker a library of 250 unmatched franchises would spend
a thousand requests per pass proving the same absence. The consequence is
stated rather than hidden -- newly-added upstream art is picked up on cache
loss, and deleting the cache directory is the way to re-check.

**Licence, unchanged and not re-litigated here.** ``Default-Images`` carries no
LICENSE file, deliberately: asked directly, the maintainer said "nearly all the
default images are based on other work, so I'm not sure it's reasonable or
valid to apply a license to derivative works" (per the maintainer on Discord,
2026-08-29). This is a private single-operator deployment, the images are
fetched at runtime rather than vendored, and it replaces a tool that does
exactly the same thing. That is ``collections/posters.py``'s module docstring's
posture verbatim, and it is the posture here; if this repository is ever
published, revisit both alongside ``assets/fonts/PROVENANCE.md`` and
``assets/badges/PROVENANCE.md``.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from autoposter.collections.posters import DEFAULT_IMAGES_BASE, fetch_poster

logger = logging.getLogger(__name__)

__all__ = [
    "FAMILIES",
    "RESOLUTION_KEYS",
    "STREAMING_NAMES",
    "UNIVERSE_CODES",
    "Family",
    "candidate_urls",
    "default_image_url",
    "ensure_default_image",
]


@dataclass(frozen=True)
class Family:
    """One family's upstream directory and how that directory is named.

    ``directory`` is the path under the repository root, spelled in full --
    ``genre`` for a flat family, ``network/color`` for a Pattern B one. Never
    derived, because the two shapes have no rule connecting them.

    ``variants`` says whether the display-name ladder applies. It is True only
    where OUR key is a name we may have decorated and upstream's is a name
    Kometa chose -- the franchise family, in practice. A code family gets a
    single candidate: 'fi-FI' is not 'fi' by any rule upstream publishes, and
    inventing one would fetch a plausible wrong flag.
    """

    directory: str
    key_scheme: str
    variants: bool
    note: str


FAMILIES: dict[str, Family] = {
    "franchise": Family(
        "franchise", "display name", True,
        "Probe §1: flat, 116 entries, exact display name. Ours are TMDb's "
        "with the pack's ' Collection' suffix stripped; upstream's are "
        "Kometa's own bucket names, so this is the one family whose ladder "
        "earns its keep -- and the one where a miss is ordinary rather than "
        "surprising.",
    ),
    "genre": Family(
        "genre", "display name", False,
        "Probe §5 `genre/`: flat, 278 entries, exact display name. Our keys "
        "are Kometa's own include-list names (`packs.GENRE_PARAMS`), which is "
        "the same vocabulary that named these files.",
    ),
    "studio": Family(
        "studio", "display name", False,
        "Probe §5 `studio/`: flat, 491 entries, exact display name. Our keys "
        "are Kometa's own 485-name include list.",
    ),
    "country": Family(
        "country/color", "display name", False,
        "Probe §4/§6: Pattern B -- the base directory holds only an index "
        "image and the full poster is the `color/` rendering. Our keys are "
        "Kometa's own 255-name include list.",
    ),
    "network": Family(
        "network/color", "display name", False,
        "Probe §4/§6: Pattern B. Our keys are Kometa's own 272-name include "
        "list. The probe measured that `color/`, `white/` and `logos/` do not "
        "hold identical sets, which is why only `color/` is ever read.",
    ),
    "streaming": Family(
        "streaming/color", "display name", False,
        "Probe §4/§6: Pattern B. Our key is not a title -- it is the TMDb "
        "watch-provider id the definition carries, mapped through "
        "`STREAMING_NAMES` -- because the builder is the generic "
        "`tmdb_discover` and the provider id is the only thing on it that "
        "names the service.",
    ),
    "audio_language": Family(
        "audio_language", "iso code", False,
        "Probe §2: flat, lowercase ISO 639-1/639-2 codes. Our keys are Plex's "
        "own language keys, which is the same vocabulary -- and where it is "
        "not (a locale variant such as `es-419`), the 404 fallback is the "
        "answer rather than a normalisation nobody published.",
    ),
    "subtitle_language": Family(
        "subtitle_language", "iso code", False,
        "Probe §3: `audio_language`'s sibling in every respect, upstream as "
        "well as here.",
    ),
    "decade": Family(
        "decade", "number", False,
        "Probe §5 `decade/`: flat decade numbers, `1880.jpg` onward. Our key "
        "is `choice.key` (`1980`), not `choice.title` (`1980s`) -- the split "
        "`DynamicType.key_from` exists to keep straight. `best/` is a "
        "'best of' variant and is not read.",
    ),
    "year": Family(
        "year", "number", False,
        "Probe §5 `year/`: flat year numbers, 153 entries. The `time_year` "
        "pack is GATED and builds nothing today, but `type: year` is a "
        "shipped dynamic type an operator can write by hand, so the row is "
        "filled -- and the pack flipping READY later costs nothing.",
    ),
    "resolution": Family(
        "resolution", "short code", False,
        "Probe §5 `resolution/`: flat labels, `4k.jpg`/`1080.jpg`/`720.jpg`/"
        "`480.jpg`. Reached two ways: the shipped `type: resolution` dynamic "
        "row keys on `choice.key`, and the `media_resolution` pack's four "
        "`plex_all` definitions key on their own `resolution` filter's first "
        "value -- the same four strings either way. `standards/` was never "
        "drilled into and is not read.",
    ),
    "universe": Family(
        "universe", "short code", False,
        "Probe §5 `universe/` and §6's full listing: flat, 22 entries, short "
        "lowercase codes rather than display names. Our key is the LIST REF "
        "the definition carries, mapped through `UNIVERSE_CODES` -- the three "
        "universe builders are generic list builders and the ref is the only "
        "thing on them that names the universe.",
    ),
}


# Our universe collections' list refs -> upstream's short code. Written from
# the §6 listing and this repository's own catalog rows (`catalog._UNIVERSE_LISTS`
# and `catalog._DC_LISTS`) -- a ref on one side, a filename on the other, and
# nothing inferred in between. A universe with no upstream entry is ABSENT here
# rather than mapped to a near miss: 'In Association With DC' is not `dca`
# (which §5 names as DC ANIMATED), and giving it that art would be a plausible
# wrong poster, which is worse than none.
UNIVERSE_CODES: dict[str, str] = {
    "ls543971628": "avp",         # Alien / Predator
    "ls566667558": "arrow",       # Arrowverse
    "ls068768438": "conjuring",   # Conjuring Universe
    "ls4102351575": "fast",       # Fast & Furious
    "ls539646485": "mcu",         # Marvel Cinematic Universe
    "8642250": "dcu",             # DC Universe (TMDb list)
    # T1 STEP 3 FILLS THESE FROM THE §6 LISTING -- Star Trek (ls547463722),
    # Star Wars Universe (ls501373412), X-Men Universe (ls567618635) and the
    # DC Extended Universe (mdblist fa11en82/dc-extended-universe). A code the
    # listing does not show is LEFT OUT, not guessed.
}


# TMDb watch-provider id -> upstream's service filename. The ids are this
# repository's own (`catalog._STREAMING_SERVICES`); the names are §6's listing.
# The two compound ids are written whole, exactly as the catalog carries them:
# `531|1770` is one collection ("either of these providers"), not two.
STREAMING_NAMES: dict[str, str] = {
    "350": "Apple TV",
    "1759": "BET+",
    "283": "Crunchyroll",
    "510": "discovery+",
    "337": "Disney+",
    "1899": "HBO Max",
    "223": "hayu",
    "15": "Hulu",
    "8": "Netflix",
    "531|1770": "Paramount+",
    "387": "Peacock",
    "9": "Prime Video",
    "528|1854": "AMC+",
    "188": "YouTube",
    "73": "tubi",
}


# The `media_resolution` pack's four bucket keys, which are also the four
# filenames §5 `resolution/` shows. A `plex_all` definition filtered on some
# other resolution value gets no poster rather than a guessed one.
RESOLUTION_KEYS = frozenset({"4k", "1080", "720", "480"})


# What our own titles may carry that upstream's filenames do not. Each entry is
# backed by a §1 sample: `Mission Impossible.jpg` against TMDb's
# 'Mission: Impossible', `Alien Predator.jpg` against our 'Alien / Predator',
# and the bare `Alien.jpg`/`Batman.jpg` against a decorated form. Applied only
# where `Family.variants` is set, and only AFTER the exact name has been tried,
# so a variant can never shadow a real hit.
_SUFFIXES = (" Collection", " Universe", " Saga", " Franchise")
_SUBSTITUTIONS = ((": ", " "), (" / ", " "), (":", ""))


def _variant_names(key: str) -> tuple[str, ...]:
    """``key`` and every documented strip-variant of it, in order, deduped."""
    seen = [key]
    for suffix in _SUFFIXES:
        for name in list(seen):
            if name.endswith(suffix) and len(name) > len(suffix):
                stripped = name[: -len(suffix)]
                if stripped not in seen:
                    seen.append(stripped)
    for old, new in _SUBSTITUTIONS:
        for name in list(seen):
            replaced = name.replace(old, new)
            if replaced != name and replaced not in seen:
                seen.append(replaced)
    return tuple(seen)


def candidate_urls(family: str, key: str) -> tuple[str, ...]:
    """Every URL this family/key could be at, exact first, or ``()``.

    An unrecognised family or an empty key answers with nothing rather than
    guessing -- ``hosted_poster_url``'s own rule, for its own reason: a wrong
    URL 404s and the collection quietly keeps no poster, which is harder to
    spot than an error.
    """
    row = FAMILIES.get(family)
    if row is None or not key:
        return ()
    names = _variant_names(key) if row.variants else (key,)
    return tuple(
        "%s/%s/%s.jpg" % (DEFAULT_IMAGES_BASE, row.directory, quote(name, safe=""))
        for name in names
    )


def default_image_url(family: str, key: str) -> str | None:
    """The first URL this family/key could be at, or ``None``.

    Pure, like everything in ``posters.py``'s three decisions: no request is
    made here. ``ensure_default_image`` is what walks the rest of the ladder.
    """
    urls = candidate_urls(family, key)
    return urls[0] if urls else None


def _cache_root(config) -> Path:
    """``<assets_root>/.generated/collection-posters`` -- beside the separator
    cache and for its reason: deliberately NOT the operator-override layout, so
    a fetched default can never masquerade as a hand-placed file."""
    return Path(config.assets_root) / ".generated" / "collection-posters"


def _cache_paths(config, family: str, key: str) -> tuple[Path, Path]:
    """Where this family/key's image and its miss marker live.

    The stem is OUR key percent-encoded with nothing safe. A collection title
    is not a value this service chose -- it comes from operator config or from
    a collection adopted out of Plex, where ``/`` and ``..`` are both legal --
    and encoding every separator is what makes the stem incapable of adding a
    path segment (the same objection ``posters._poster_candidates`` answers
    with its realpath containment).
    """
    folder = _cache_root(config) / family
    stem = quote(key, safe="")
    return folder / (stem + ".jpg"), folder / (stem + ".miss")


async def ensure_default_image(config, http, family: str, key: str) -> Path | None:
    """This collection's cached default poster file, or ``None``.

    Cache first (a cached file is final -- see the module docstring), then one
    validated fetch per candidate name, stopping at the first that answers with
    a real image. Every failure -- an unknown family, no client and no cached
    file, a 404 on every candidate, a body no decoder accepts -- returns
    ``None`` and leaves no partial file, and the caller then reports "no poster
    source" exactly as it does today.

    ``http`` may be ``None``: that refuses the FETCH only, so a family whose
    image is already cached still resolves. Same posture as
    ``separator_art.ensure_separator_art``.
    """
    urls = candidate_urls(family, key)
    if not urls:
        return None
    target, marker = _cache_paths(config, family, key)
    if target.is_file():
        return target
    if marker.is_file() or http is None:
        return None
    for url in urls:
        data = await fetch_poster(http, url)
        if data is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target
    logger.debug(
        "no Default-Images asset for %s %r (tried %d candidate name(s))",
        family, key, len(urls),
    )
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"")
    return None
```

Then fill `UNIVERSE_CODES`' four commented rows from `.superpowers/sdd/p-defimg-probe.md` §6's `universe/` listing, deleting the `T1 STEP 3 FILLS THESE` comment. A universe whose code the listing does not show stays out, and the task's report says which.

- [ ] **Step 8: Run the mapping tests and verify they pass**

```bash
docker compose -p pdi1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_default_images.py 2>&1 | tee /app/.superpowers/run-pdi1-green.log"
docker compose -p pdi1 down
tail -20 .superpowers/run-pdi1-green.log
```

Expected: `13 passed`. A failure in `test_the_franchise_variant_ladder_tries_the_exact_name_first` means `_variant_names` ordering broke — exact must be `[0]`.

- [ ] **Step 9: Write the failing test for `apply_poster`'s default-image branch**

Append to `tests/test_collection_poster_apply.py`:

```python
async def test_a_default_image_family_is_fetched_cached_and_reported_as_such(
    tmp_path, config_factory, session
):
    """The new fourth source. It rides the same rank the generated separator
    art already occupies -- below the operator's own file, above nothing --
    so the priority guarantee `prioritize_assets` gave is unchanged."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        assert request.url.path.endswith("/genre/Action.jpg")
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            "genre", "Action", dry_run=False,
        )

    assert message == "set the poster for %r from the hosted default image" % TITLE
    assert collection.uploaded_bytes == [data]
    assert record.poster_sha256 == hashlib.sha256(data).hexdigest()
    assert (
        tmp_path / ".generated" / "collection-posters" / "genre" / "Action.jpg"
    ).is_file()


async def test_a_local_override_beats_a_default_image_and_makes_no_request(
    tmp_path, config_factory, session
):
    """Constraint 4, pinned on the new branch: the operator's file is read
    first, unconditionally, before `kind`/`key` is consulted."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    local = tmp_path / LIBRARY / TITLE
    local.mkdir(parents=True)
    mine = _jpeg_bytes("blue")
    (local / "poster.jpg").write_bytes(mine)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        raise AssertionError("a local override must not be fetched over")

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            "genre", "Action", dry_run=False,
        )

    assert collection.uploaded_bytes == [mine]
    assert "local file" in message


async def test_a_default_image_that_404s_leaves_the_collection_exactly_as_today(
    tmp_path, config_factory, session
):
    """The fallback that makes the whole feature safe to switch on: a family
    with no matching asset renders today's behaviour -- no upload, no hash
    written, the same 'no poster source' report a `kind=None` collection gets."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    record = await _record(session)
    collection = _FakeCollection()

    async def handler(request):
        return httpx.Response(404)

    async with _client(handler) as http:
        message = await apply_poster(
            session, http, config, collection, record, LIBRARY,
            "franchise", "A Franchise Kometa Never Drew", dry_run=False,
        )

    assert message == "no poster source for %r" % TITLE
    assert collection.uploaded_bytes == []
    assert record.poster_sha256 is None


async def test_the_six_original_kinds_are_untouched_by_the_new_branch(
    tmp_path, config_factory, session
):
    """`hosted_poster_url`'s table is not extended by this phase, and a chart
    still goes straight down the old path with no cache file written."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    record = await _record(session)
    collection = _FakeCollection()
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=data)

    async with _client(handler) as http:
        await apply_poster(
            session, http, config, collection, record, LIBRARY, KIND, KEY,
            dry_run=False,
        )

    assert seen == [hosted_poster_url(KIND, KEY)]
    assert not (tmp_path / ".generated").exists()
```

- [ ] **Step 10: Run those four and verify they fail**

```bash
docker compose -p pdi1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_poster_apply.py 2>&1 | tee /app/.superpowers/run-pdi1-red2.log"
docker compose -p pdi1 down
tail -30 .superpowers/run-pdi1-red2.log
```

Expected: `4 failed` — the first three because `kind="genre"`/`"franchise"` currently falls through `hosted_poster_url` to `None` ("no poster source"), the fourth passing already or failing only on the `.generated` assertion. Any other failure count means the file's existing tests broke; investigate before continuing.

- [ ] **Step 11: Add the branch to `apply_poster`**

In `src/autoposter/collections/posters.py`, add the import at the top of the function body's module imports — a LOCAL import inside `apply_poster`, because `default_images` imports `DEFAULT_IMAGES_BASE` and `fetch_poster` from this module and a module-level import here would be a cycle:

Replace the `if data is None and generated is not None:` block and the block after it with:

```python
    if data is None:
        # A file some source already produced, below the operator's own
        # override and above anything fetched fresh: generated separator art
        # (``separator_art.py``) or a cached Default-Images family poster
        # (``default_images.py``). Both are poster SOURCES, not overrides, so
        # ``prioritize_assets``' guarantee is unchanged -- the local branch
        # above has already had its say.
        #
        # Imported here rather than at module scope: ``default_images`` reads
        # ``DEFAULT_IMAGES_BASE`` and ``fetch_poster`` from THIS module, and a
        # top-level import would be a cycle. The one call is per collection,
        # not per item.
        from autoposter.collections import default_images

        cached = generated
        label = "generated separator art"
        if cached is None and kind in default_images.FAMILIES:
            cached = await default_images.ensure_default_image(
                config, http, kind, key,
            )
            label = "the hosted default image"
        if cached is not None:
            try:
                candidate = cached.read_bytes()
            except OSError:
                candidate = b""
            if _is_image(candidate):
                data = candidate
                source = label
            else:
                logger.info(
                    "%s %s did not decode as an image; using whatever source "
                    "kind names", label, cached,
                )
```

Leave the `if data is None:` hosted/TMDb-profile block that follows exactly as it is. A default-image family whose fetch failed reaches it, `hosted_poster_url` does not know the kind, and it returns `"no poster source for %r"` — which is precisely the fallback the tests pin.

Update `apply_poster`'s docstring resolution-order sentence to name the third source:

```python
    """Give ``collection`` its poster, uploading only when something changed.

    Resolution order: a local override first (read directly off disk, no
    request made), a file some source already produced second when one
    resolves -- the caller's generated separator art
    (``collections/separator_art.py``), or this collection's family poster
    cached from ``Kometa-Team/Default-Images`` (``collections/default_images.py``),
    both read directly off disk -- the source ``kind`` names third (a hosted
    default from the six-kind table, or a person's TMDb profile photo), nothing
    fourth. ...
```

- [ ] **Step 12: Run the apply tests and verify they pass**

```bash
docker compose -p pdi1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_poster_apply.py tests/test_collection_default_images.py 2>&1 | tee /app/.superpowers/run-pdi1-green2.log"
docker compose -p pdi1 down
tail -20 .superpowers/run-pdi1-green2.log
```

Expected: `0 failed`.

- [ ] **Step 13: Write the failing franchise wiring test**

Append to `tests/test_collection_poster_wiring.py`:

```python
async def test_the_franchise_builder_names_its_own_collection_as_the_poster_key():
    """Upstream keys franchise art by NAME, never by TMDb collection id
    (p-defimg-probe.md §1 and its 'Confirmed non-findings' section), and the
    name we have is the unit's own title -- TMDb's collection name with the
    pack's ' Collection' suffix already stripped by `family_titles`."""
    from autoposter.collections.builders.base import BuilderContext, BuilderResult
    from autoposter.collections.builders.tmdb import TmdbCollectionBuilder

    class _Client:
        async def collection_parts(self, collection_id):
            return ["603"]

    class _Sources:
        tmdb = _Client()

    ctx = BuilderContext(
        library="Movies", library_type="Movie",
        config={"id": 1241},
        sources=_Sources(),
        definition=CollectionDefinition(
            title="Harry Potter", builder="tmdb_collection", params={"id": 1241},
        ),
    )
    result = await TmdbCollectionBuilder().build(ctx)

    assert isinstance(result, BuilderResult)
    assert result.poster_kind == "franchise"
    assert result.poster_key == "Harry Potter"


async def test_a_franchise_built_without_a_definition_offers_no_poster_key():
    """A direct caller has no definition (`BuilderContext.definition` is None
    for one), and a poster key invented from an id would be a URL that 404s."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.tmdb import TmdbCollectionBuilder

    class _Client:
        async def collection_parts(self, collection_id):
            return ["603"]

    class _Sources:
        tmdb = _Client()

    ctx = BuilderContext(
        library="Movies", library_type="Movie",
        config={"id": 1241}, sources=_Sources(),
    )
    result = await TmdbCollectionBuilder().build(ctx)

    assert result.poster_kind is None
    assert result.poster_key is None
```

- [ ] **Step 14: Run them and verify they fail**

```bash
docker compose -p pdi1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_poster_wiring.py 2>&1 | tee /app/.superpowers/run-pdi1-red3.log"
docker compose -p pdi1 down
tail -20 .superpowers/run-pdi1-red3.log
```

Expected: `1 failed` — `test_the_franchise_builder_names_its_own_collection_as_the_poster_key`, on `assert None == 'franchise'`. The second test passes already (both fields are `None` today); it is there to stop a future implementation from reaching for the id.

- [ ] **Step 15: Wire the franchise builder**

In `src/autoposter/collections/builders/tmdb.py`, replace `TmdbCollectionBuilder`:

```python
class TmdbCollectionBuilder(_TmdbBuilder):
    """A franchise collection's parts. Movie libraries only: TMDb collections
    are movie franchises and their ``parts`` are movies.

    The one builder here that carries a poster key, and it is the collection's
    own TITLE rather than its id. ``Kometa-Team/Default-Images`` keys franchise
    art by display name -- ``franchise/Jurassic Park.jpg`` -- and holds no
    id-based naming anywhere in the repository
    (``.superpowers/sdd/p-defimg-probe.md`` §1, and its "Confirmed
    non-findings" section). For a unit the ``content_franchises`` pack expanded,
    the definition's title IS TMDb's own collection name with the pack's
    ``remove_suffix: [' Collection']`` applied and any ``title_override``
    honoured, so it is both the best name we have and the one an operator can
    correct by hand. Upstream curates 116 franchises against TMDb's whole
    collection space, so a MISS is ordinary: ``default_images`` answers None,
    and the collection keeps whatever poster it had.

    A direct caller has no definition, and a key invented from the id would be
    a URL that 404s quietly -- so both fields stay None there.
    """

    type_name = "tmdb_collection"
    params_model = TmdbEntityParams

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbEntityParams.model_validate(ctx.config)
        require_library_type("the 'tmdb_collection' builder", ctx.library_type, ("Movie",))
        client = self._client(ctx)
        ids = await client.collection_parts(params.id)
        title = getattr(ctx.definition, "title", None)
        return BuilderResult(
            ids=[("tmdb", value) for value in ids],
            poster_kind="franchise" if title else None,
            poster_key=title or None,
        )
```

Also update the module docstring's "No summary and no poster" paragraph, which is now false for one of the five:

```python
- **That absence is an error.** No token means no client means raise, per
  ``SourceClients``; a 404 raises out of the client for the same reason. An
  empty membership one layer down means "remove every member".

No summary, and a poster on one builder only: ``tmdb_chart``'s title and summary
would need Kometa translation strings, and no such transcription exists for
TMDb's charts the way ``docs/research/kometa-collections.md`` §5 holds IMDb's --
so a summary invented here would not be parity, and a guessed poster key is a
hosted URL that 404s and leaves the collection quietly without artwork
(``posters.hosted_poster_url``). The definition's own ``summary:`` is the way to
set one until the strings are recorded. ``tmdb_collection`` is the exception on
the poster half alone, and only because upstream's franchise art is keyed by a
name the definition already carries; see that builder.
```

- [ ] **Step 16: Run the wiring tests and verify they pass**

```bash
docker compose -p pdi1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_poster_wiring.py tests/test_collection_posters.py 2>&1 | tee /app/.superpowers/run-pdi1-green3.log"
docker compose -p pdi1 down
tail -20 .superpowers/run-pdi1-green3.log
```

Expected: `0 failed`.

- [ ] **Step 17: Full suite, golden gate, and commit**

```bash
docker compose -p pdi1c -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pdi1c-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pdi1-full.log"
docker wait pdi1c-full
tail -10 .superpowers/run-pdi1-full.log
docker compose -p pdi1c down
git status --porcelain tests/fixtures/collections/golden_port.json
```

Expected: `0 failed`, and the golden fixture line prints NOTHING (byte-identical, Constraint 12).

**Stated delta:** baseline + 19 tests (13 new in `test_collection_default_images.py`, 4 in `test_collection_poster_apply.py`, 2 in `test_collection_poster_wiring.py`), + 1 test file. Measure against the Step 3 baseline and record both numbers in the commit body. A mismatch is investigated, never overwritten.

```bash
git add src/autoposter/collections/default_images.py \
        src/autoposter/collections/posters.py \
        src/autoposter/collections/builders/tmdb.py \
        tests/test_collection_default_images.py \
        tests/test_collection_poster_apply.py \
        tests/test_collection_poster_wiring.py \
        tests/conftest.py
git commit --no-gpg-sign -m "feat(collections): Default-Images family posters, cached, with franchises wired"
```

---

### Task 2: The dynamic-family hook — nine families through one column

**Files:**
- Modify: `src/autoposter/collections/dynamic_types.py`
- Modify: `src/autoposter/collections/smart.py`
- Modify: `src/autoposter/collections/builders/dynamic.py`
- Modify: `tests/test_collection_dynamic_types.py`
- Modify: `tests/test_collection_poster_wiring.py`

**Interfaces:**
- Consumes from Task 1: `default_images.FAMILIES` (the kind vocabulary `apply_poster` dispatches on) — this task supplies kinds from that dict and nothing else.
- Produces, and Task 3 does not depend on it:
  - `DynamicType.poster_kind: str | None = None` — the new last column of every row in `DYNAMIC_TYPES`.
  - `reconcile_smart_collection(..., poster_kind: str | None = None, poster_key: str | None = None)` — two new keyword-only-in-practice parameters, defaulted so every existing caller (the engine's smart dispatch, `cs_bucket`, `smart_filter`, `credits_family`) is unchanged.

- [ ] **Step 1: Write the failing table test**

Append to `tests/test_collection_dynamic_types.py`:

```python
def test_every_poster_kind_names_a_family_default_images_actually_holds():
    """The column is a cross-module contract: a kind `default_images` does not
    know is a poster that silently never resolves, which is exactly the class
    of quiet failure `hosted_poster_url`'s own refusal rule exists to stop."""
    from autoposter.collections.default_images import FAMILIES

    for row in DYNAMIC_TYPES.values():
        if row.poster_kind is not None:
            assert row.poster_kind in FAMILIES, row.name


def test_the_nine_families_with_upstream_art_carry_it_and_content_rating_does_not():
    """The table's own law, from p-defimg-probe.md's summary table: every
    dynamic type this service ships has a Default-Images directory EXCEPT
    content_rating, whose art is region-scoped (`content_rating/<region>/`) and
    whose region a dynamic definition does not name. The `cs_bucket` family is
    already wired to the `cs` region by hand; picking a region for an operator
    here would be a guess that resolves to plausible wrong artwork."""
    assert {
        name: row.poster_kind for name, row in DYNAMIC_TYPES.items()
    } == {
        "year": "year",
        "decade": "decade",
        "content_rating": None,
        "studio": "studio",
        "genre": "genre",
        "country": "country",
        "resolution": "resolution",
        "audio_language": "audio_language",
        "subtitle_language": "subtitle_language",
        "network": "network",
    }
```

- [ ] **Step 2: Run it and verify it fails**

```bash
docker compose -p pdi2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_dynamic_types.py 2>&1 | tee /app/.superpowers/run-pdi2-red1.log"
docker compose -p pdi2 down
tail -20 .superpowers/run-pdi2-red1.log
```

Expected: `2 failed` with `AttributeError: 'DynamicType' object has no attribute 'poster_kind'`.

- [ ] **Step 3: Add the column and fill the rows**

In `src/autoposter/collections/dynamic_types.py`, add the field to `DynamicType` after `note` (last, and defaulted, so no row's positional arguments move):

```python
    name: str
    attribute: str
    search_key: str
    kinds: tuple[str, ...]
    key_from: str
    title_format: str
    sort_by: tuple[str, ...]
    limit: int | None
    note: str
    # Which ``collections/default_images.FAMILIES`` row supplies this family's
    # default poster, keyed by the unit's own ``key`` -- which is why the
    # column sits HERE rather than in the builder: the key a bucket is derived
    # under is the key upstream named the file by, and the two must not be two
    # decisions. None means "no upstream art we can address", which is a fact
    # about the repository rather than a switch: see ``content_rating``.
    poster_kind: str | None = None
```

Add the poster kind to each row's constructor call as a trailing keyword argument. `year`:

```python
        DynamicType(
            "year", "year", "year", _BOTH, "title",
            "Best <<library_type>>s of <<key_name>>", _GENERAL_SORT, _GENERAL_LIMIT,
            "meta.py:955 for the title, :919-923 and :936 for the enumeration. "
            "The type whose fan-out the roadmap's own risk note names: a "
            "70-year library is 70 collections, which is why the builder's "
            "``max_collections`` floor defaults below that. The phase's probe "
            "measured 87 on the production movie library and 39 on the shows, "
            "and found a WORSE case than this one -- see ``studio``.",
            poster_kind="year",
        ),
```

`decade` → `poster_kind="decade"`; `studio` → `"studio"`; `genre` → `"genre"`; `country` → `"country"`; `resolution` → `"resolution"`; `audio_language` → `"audio_language"`; `subtitle_language` → `"subtitle_language"`; `network` → `"network"`. `content_rating` takes `poster_kind=None` **written explicitly**, with this sentence appended to its existing note:

```python
            "No poster kind, deliberately: upstream's content-rating art is "
            "region-scoped (``content_rating/<region>/<rating>.jpg``) and a "
            "dynamic definition names no region. The Common Sense family this "
            "row generalises is already wired to the ``cs`` region by hand "
            "(``reconcile.py``); choosing a region for an operator here would "
            "resolve to another country's rating artwork, which is a plausible "
            "wrong poster rather than an absent one.",
            poster_kind=None,
```

Add one paragraph to the module docstring, after the "One documented divergence" section:

```
**The poster column.** Every row but ``content_rating`` names a
``Default-Images`` directory whose per-member art is keyed by the same string
this table's ``key_from`` column already produces -- ISO codes for the two
language rows, ``choice.key`` numbers for ``decade`` and ``year``, the label
for ``resolution``, and Kometa's own include-list names for the four tag rows.
That coincidence is not luck: our keys ARE upstream's grouping vocabulary,
transcribed in ``collections/packs.py``, and upstream named the files by it.
The directory-per-family table and the fetch live in
``collections/default_images.py``; this column is only which row of it a family
takes. Where no asset matches, the collection keeps whatever poster it had.
```

- [ ] **Step 4: Run the table test and verify it passes**

```bash
docker compose -p pdi2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_dynamic_types.py 2>&1 | tee /app/.superpowers/run-pdi2-green1.log"
docker compose -p pdi2 down
tail -20 .superpowers/run-pdi2-green1.log
```

Expected: `0 failed`.

- [ ] **Step 5: Write the failing end-to-end wiring test**

Append to `tests/test_collection_poster_wiring.py`:

```python
async def test_a_dynamic_family_unit_gets_its_upstream_poster(
    session, section_factory, config_factory, tmp_path
):
    """The hook the whole dynamic half of this phase hangs on. Before it,
    `smart.reconcile_smart_collection` passed literal `None, None` to
    `apply_poster` for EVERY dynamic family -- so genre, studio, country,
    network, decade and both language families had no poster source at all
    beyond an operator's own file. The key is the unit's own, which for
    `audio_language` is the ISO code p-defimg-probe.md §2 shows the files are
    named by."""
    from autoposter.collections.smart import reconcile_smart_collection

    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    data = _jpeg_bytes()
    seen = []

    async def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=data)

    section = section_factory()
    async with _client(handler) as http:
        await reconcile_smart_collection(
            session, section, "Movies", "Movie", "Top Finnish Movies",
            "?type=1&audioLanguage=fi", LABEL,
            dry_run=False, http=http, config=config,
            poster_kind="audio_language", poster_key="fi",
        )

    assert seen == [
        DEFAULT_IMAGES_BASE + "/audio_language/fi.jpg"
    ]
    row = (await session.execute(
        select(ManagedCollection).where(ManagedCollection.title == "Top Finnish Movies")
    )).scalar_one()
    assert row.poster_sha256 is not None


async def test_a_smart_definition_with_no_family_still_has_no_poster_source(
    session, section_factory, config_factory, tmp_path
):
    """The default is unchanged for every caller that does not pass the pair --
    `smart_filter`, `cs_bucket`'s own path, `credits_family`, and the engine's
    plain smart dispatch. A definition with no builder to derive artwork from
    still says so, honestly, on every pass."""
    from autoposter.collections.smart import reconcile_smart_collection

    config = config_factory(assets_root=str(tmp_path), library_folders=True)

    async def handler(request):
        raise AssertionError("a family-less smart collection must fetch nothing")

    section = section_factory()
    async with _client(handler) as http:
        actions = await reconcile_smart_collection(
            session, section, "Movies", "Movie", "Hand-written", "?type=1",
            LABEL, dry_run=False, http=http, config=config,
        )

    assert any("no poster source" in action for action in actions)


async def test_the_leftovers_bucket_is_never_given_a_family_poster():
    """`dynamic_titles.OTHER_KEY` is the string 'other', and `aspect/other.jpg`
    exists upstream -- so an unguarded lookup would ask for
    `country/color/other.jpg` for 'Other Countries' and, if the families were
    ever merged, hand it the aspect-ratio catch-all's art. The bucket is a name
    this service invented; upstream never drew it."""
    from autoposter.collections.builders.dynamic import poster_for_unit
    from autoposter.collections.dynamic_titles import OTHER_KEY, TitledKey
    from autoposter.collections.dynamic_types import DYNAMIC_TYPES

    row = DYNAMIC_TYPES["country"]
    ordinary = TitledKey(key="France", title="France", values=("France",))
    leftovers = TitledKey(
        key=OTHER_KEY, title="Other Countries", values=("Sealand",)
    )

    assert poster_for_unit(row, ordinary) == ("country", "France")
    assert poster_for_unit(row, leftovers) == (None, None)
    assert poster_for_unit(DYNAMIC_TYPES["content_rating"], ordinary) == (None, None)
```

If `tests/test_collection_poster_wiring.py` has no `section_factory` fixture in scope, use the same fake-section construction the file's existing smart tests use — read the file's top-of-module helpers and reuse them rather than inventing a second fake.

- [ ] **Step 6: Run them and verify they fail**

```bash
docker compose -p pdi2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_poster_wiring.py 2>&1 | tee /app/.superpowers/run-pdi2-red2.log"
docker compose -p pdi2 down
tail -30 .superpowers/run-pdi2-red2.log
```

Expected: `2 failed` — `TypeError: reconcile_smart_collection() got an unexpected keyword argument 'poster_kind'` and `ImportError: cannot import name 'poster_for_unit'`. The middle test passes already; it is the regression guard for every existing caller.

- [ ] **Step 7: Thread the pair through `reconcile_smart_collection`**

In `src/autoposter/collections/smart.py`, add two parameters at the end of `reconcile_smart_collection`'s signature (after `sort_prefix`):

```python
    sort_prefix: str | None = None,
    poster_kind: str | None = None,
    poster_key: str | None = None,
) -> list[str]:
```

Add this paragraph to its docstring, immediately before the closing `"""`:

```
    ``poster_kind``/``poster_key`` name this collection's default artwork, the
    way ``BuilderResult``'s two fields of the same name do on the list path.
    They default to None and every caller that passes nothing keeps exactly the
    behaviour this function has always had -- a ``smart_filter`` definition
    genuinely has no builder to derive artwork from, and says so on every pass.
    The FAMILY builders are what fill them: a family's key is derived under the
    same vocabulary upstream named its files by, so the family builder is the
    one caller that can answer, and it is the only one that does.
```

Replace the `apply_poster` call block:

```python
    if posters_on and collection is not None and record is not None:
        # ``kind``/``key`` come from the caller now. A ``smart_filter``
        # definition still passes neither -- it has no builder to derive
        # default artwork from, so ``hosted_poster_url`` has nothing to offer
        # and only the operator's LOCAL override (keyed on library+title) can
        # supply one, which is the same shape a ``plex_id`` collection has and
        # means "no poster source" on every pass until a local file exists.
        # A FAMILY builder passes both, and its key is the one upstream named
        # the file by (``collections/default_images.py``); where upstream holds
        # no such file the fetch 404s and the report is that same honest line.
        message = await apply_poster(
            session, http, config, collection, record, library,
            poster_kind, poster_key, dry_run=dry_run,
        )
        if message:
            actions.append(message)
```

- [ ] **Step 8: Add the per-unit resolution and pass it**

In `src/autoposter/collections/builders/dynamic.py`, add this module-level function beside the other module functions (above `DynamicBuilder`), and export it in `__all__`:

```python
def poster_for_unit(row, unit) -> tuple[str | None, str | None]:
    """This unit's default-poster family and key, or ``(None, None)``.

    The key is the unit's OWN key, never its title: the two differ on every row
    whose ``key_from`` is ``"key"`` (``1980`` against "the 1980s", ``4k``
    against "4K"), and it is the key half that upstream named its files by.

    The leftovers bucket is excluded by name. ``other`` is a bucket THIS
    SERVICE invents for the keys no ``include:`` entry claimed; upstream never
    drew it, and a family directory that happens to hold an ``other.jpg`` for
    its own reasons (``aspect/`` does) would answer with artwork belonging to
    a different question entirely.
    """
    if row.poster_kind is None or unit.key == OTHER_KEY:
        return None, None
    return row.poster_kind, unit.key
```

`OTHER_KEY` is already imported in that module (the refusal branch compares against it). In the per-unit loop, pass the pair to `reconcile_smart_collection` — add these two arguments immediately after `sort_prefix=ctx.sort_prefix,`:

```python
                    sort_prefix=ctx.sort_prefix,
                    # The family's own default artwork
                    # (``collections/default_images.py``). One call per
                    # generated key, keyed by that key -- which is the same
                    # string upstream named the file by, because both come from
                    # Kometa's own grouping vocabulary.
                    **dict(zip(
                        ("poster_kind", "poster_key"),
                        poster_for_unit(row, unit),
                    )),
                )
```

Prefer the explicit form if the reviewer finds the `zip` opaque — it is equivalent and one line longer:

```python
                    poster_kind=poster_for_unit(row, unit)[0],
                    poster_key=poster_for_unit(row, unit)[1],
```

Use the explicit two-line form. Delete the `zip` variant.

- [ ] **Step 9: Run the wiring tests and verify they pass**

```bash
docker compose -p pdi2 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_poster_wiring.py tests/test_collection_dynamic.py tests/test_collection_smart.py 2>&1 | tee /app/.superpowers/run-pdi2-green2.log"
docker compose -p pdi2 down
tail -20 .superpowers/run-pdi2-green2.log
```

Expected: `0 failed`. If `tests/test_collection_dynamic.py` or `tests/test_collection_smart.py` do not exist under those exact names, run the whole `tests/` directory instead — the full suite is the next step regardless.

- [ ] **Step 10: Full suite, golden gate, and commit**

```bash
docker compose -p pdi2b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pdi2b-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pdi2-full.log"
docker wait pdi2b-full
tail -10 .superpowers/run-pdi2-full.log
docker compose -p pdi2b down
git status --porcelain tests/fixtures/collections/golden_port.json
```

Expected: `0 failed`; the golden fixture line prints NOTHING.

**Stated delta:** T1's measured total + 5 tests (2 in `test_collection_dynamic_types.py`, 3 in `test_collection_poster_wiring.py`), no new test file.

```bash
git add src/autoposter/collections/dynamic_types.py \
        src/autoposter/collections/smart.py \
        src/autoposter/collections/builders/dynamic.py \
        tests/test_collection_dynamic_types.py \
        tests/test_collection_poster_wiring.py
git commit --no-gpg-sign -m "feat(collections): dynamic families take their Default-Images posters"
```

---

### Task 3: The remaining list-built families — streaming, resolution, universe

**Files:**
- Modify: `src/autoposter/collections/builders/tmdb_discover.py`
- Modify: `src/autoposter/collections/builders/plex_trivial.py`
- Modify: `src/autoposter/collections/builders/imdb_lists.py`
- Modify: `src/autoposter/collections/builders/mdblist.py`
- Modify: `tests/test_collection_poster_wiring.py`
- Modify: `tests/test_collection_default_images.py`

**Interfaces:**
- Consumes from Task 1: `default_images.STREAMING_NAMES`, `default_images.RESOLUTION_KEYS`, `default_images.UNIVERSE_CODES` — all three read-only, and the only place any of them is read.
- Produces: nothing later tasks depend on. Task 4 is paperwork.

**CONFIRMED-BY-T1.** This task depends on T1 Step 3's `universe/` and `streaming/color/` listings having answered 200 and on `UNIVERSE_CODES` having been completed from them. Re-read `.superpowers/sdd/p-defimg-probe.md` §6 before starting. A universe whose code §6 does not show is NOT wired and is named in the task's report — the collection keeps today's behaviour, which is the whole fallback contract.

- [ ] **Step 1: Write the failing lookup tests**

Append to `tests/test_collection_default_images.py`:

```python
def test_the_universe_codes_are_short_codes_not_display_names():
    """p-defimg-probe.md §5 `universe/` and §6's full listing: this family and
    `seasonal/` are the two that depart from display-name naming. Our universe
    collections are built by three DIFFERENT generic list builders, so the LIST
    REF is the only thing on a definition that names the universe -- which is
    why the table is keyed by ref."""
    from autoposter.collections.default_images import UNIVERSE_CODES

    assert UNIVERSE_CODES["ls539646485"] == "mcu"      # Marvel Cinematic Universe
    assert UNIVERSE_CODES["ls566667558"] == "arrow"    # Arrowverse
    assert UNIVERSE_CODES["ls543971628"] == "avp"      # Alien / Predator
    assert UNIVERSE_CODES["8642250"] == "dcu"          # DC Universe (TMDb list)
    # 'In Association With DC' has no upstream entry and must not borrow one:
    # §5 names `dca` as DC ANIMATED, a different continuity.
    assert "fa11en82/in-association-with-dc" not in UNIVERSE_CODES
    for code in UNIVERSE_CODES.values():
        assert code == code.lower()
        assert " " not in code


def test_the_streaming_table_maps_provider_ids_to_upstream_service_names():
    """Our streaming pack is `tmdb_discover` definitions carrying a TMDb
    watch-provider id (`catalog._STREAMING_SERVICES`); upstream names the files
    by service (p-defimg-probe.md §6 `streaming/color/`). The two compound ids
    stay whole -- `531|1770` is ONE collection, 'either of these providers'."""
    from autoposter.collections.default_images import STREAMING_NAMES

    assert STREAMING_NAMES["8"] == "Netflix"
    assert STREAMING_NAMES["337"] == "Disney+"
    assert STREAMING_NAMES["531|1770"] == "Paramount+"
    assert default_image_url("streaming", STREAMING_NAMES["8"]) == (
        BASE + "/streaming/color/Netflix.jpg"
    )


def test_the_resolution_keys_are_exactly_the_packs_four_buckets():
    """p-defimg-probe.md §5 `resolution/` holds nine labels; the four our
    `media_resolution` pack builds are the four we can ever ask for."""
    from autoposter.collections.default_images import RESOLUTION_KEYS

    assert RESOLUTION_KEYS == frozenset({"4k", "1080", "720", "480"})
```

Append to `tests/test_collection_poster_wiring.py`:

```python
async def test_a_streaming_definition_takes_its_service_poster():
    """`production_streaming` is built by the GENERIC `tmdb_discover` builder,
    so there is no family-specific builder to hang a kind on -- the provider id
    is the only thing on the definition that names the service."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.tmdb_discover import TmdbDiscoverBuilder

    class _Client:
        async def discover(self, media_type, filters):
            return ["603"]

    class _Sources:
        tmdb = _Client()

    ctx = BuilderContext(
        library="Movies", library_type="Movie",
        config={
            "with_watch_providers": "8",
            "watch_region": "US",
            "sort_by": "popularity.desc",
        },
        sources=_Sources(),
    )
    result = await TmdbDiscoverBuilder().build(ctx)

    assert (result.poster_kind, result.poster_key) == ("streaming", "Netflix")


async def test_a_discover_definition_with_no_watch_provider_offers_no_poster():
    """Every other `tmdb_discover` definition -- and there are many -- behaves
    exactly as it does today."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.tmdb_discover import TmdbDiscoverBuilder

    class _Client:
        async def discover(self, media_type, filters):
            return ["603"]

    class _Sources:
        tmdb = _Client()

    ctx = BuilderContext(
        library="Movies", library_type="Movie",
        config={"sort_by": "popularity.desc"}, sources=_Sources(),
    )
    result = await TmdbDiscoverBuilder().build(ctx)

    assert (result.poster_kind, result.poster_key) == (None, None)


async def test_a_resolution_bucket_takes_its_poster_from_its_own_filter():
    """`media_resolution` is four `plex_all` definitions distinguished only by
    their `resolution` filter -- the pack's own bucket key, and the same four
    strings p-defimg-probe.md §5 `resolution/` names its files by. A `plex_all`
    definition with any other filter, or none, is untouched."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.plex_trivial import PlexAllBuilder

    class _Access:
        def owned_index(self):
            return {"plex": ["1", "2"]}

    class _Sources:
        plex = _Access()

    def _ctx(filters):
        return BuilderContext(
            library="Movies", library_type="Movie", config={},
            sources=_Sources(),
            definition=CollectionDefinition(
                title="4k Movies", builder="plex_all", filters=filters,
            ),
        )

    bucket = await PlexAllBuilder().build(_ctx({"resolution": ["4k", "8k"]}))
    assert (bucket.poster_kind, bucket.poster_key) == ("resolution", "4k")

    other = await PlexAllBuilder().build(_ctx({"genre": ["Action"]}))
    assert (other.poster_kind, other.poster_key) == (None, None)

    bare = await PlexAllBuilder().build(
        BuilderContext(
            library="Movies", library_type="Movie", config={}, sources=_Sources(),
        )
    )
    assert (bare.poster_kind, bare.poster_key) == (None, None)


async def test_a_universe_list_takes_its_short_code_poster():
    """Three builders, one table. An IMDb list id, a TMDb list id and an
    MDBList ref all resolve through `UNIVERSE_CODES`; a list ref that is not a
    universe resolves to nothing, which is every other list definition."""
    from autoposter.collections.builders.base import BuilderContext
    from autoposter.collections.builders.imdb_lists import ImdbListBuilder

    async def handler(request):
        return httpx.Response(200, json={"titles": []})

    class _Sources:
        pass

    # The builder's own fetch is stubbed at the module seam it already uses;
    # this test asserts the poster pair, not the membership.
    import autoposter.collections.builders.imdb_lists as module

    async def _entries(http, list_id):
        return []

    original = module.fetch_list
    module.fetch_list = _entries
    try:
        mcu = await ImdbListBuilder().build(BuilderContext(
            library="Movies", library_type="Movie",
            config={"list": "ls539646485"}, sources=_Sources(),
        ))
        other = await ImdbListBuilder().build(BuilderContext(
            library="Movies", library_type="Movie",
            config={"list": "ls000000001"}, sources=_Sources(),
        ))
    finally:
        module.fetch_list = original

    assert (mcu.poster_kind, mcu.poster_key) == ("universe", "mcu")
    assert (other.poster_kind, other.poster_key) == (None, None)
```

- [ ] **Step 2: Run them and verify they fail**

```bash
docker compose -p pdi3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_default_images.py tests/test_collection_poster_wiring.py 2>&1 | tee /app/.superpowers/run-pdi3-red.log"
docker compose -p pdi3 down
tail -40 .superpowers/run-pdi3-red.log
```

Expected: `4 failed` in the wiring file (each on `(None, None) != (...)`) and `0 failed` in the default-images file — its three new tests read tables T1 already wrote. If any of those three fails, T1's `UNIVERSE_CODES` completion (Step 7) was not done; go back and do it from §6 rather than patching the test.

- [ ] **Step 3: Wire the streaming builder**

In `src/autoposter/collections/builders/tmdb_discover.py`, replace `TmdbDiscoverBuilder.build`'s return and add the module import `from autoposter.collections.default_images import STREAMING_NAMES` at the top:

```python
    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbDiscoverParams.model_validate(ctx.config)
        require_library_type(
            f"the {self.type_name!r} builder", ctx.library_type, self.media_types
        )
        media_type = self.media_types[ctx.library_type]
        filters = discover_filters(params, media_type, ctx.library_type)
        client = self._client(ctx)
        ids = await client.discover(media_type, filters)
        # The streaming pack's fifteen definitions differ from every other
        # discover definition -- and from each other -- by exactly one field:
        # the watch-provider id. That id is what names the service, so it is
        # what resolves the poster; a discover definition that carries no
        # provider is not a streaming collection and keeps no default artwork.
        # Upstream keys the files by service NAME
        # (``.superpowers/sdd/p-defimg-probe.md`` §6), which the table
        # translates, and a provider the table does not name gets None rather
        # than a URL built from a number upstream never used.
        service = STREAMING_NAMES.get(str(ctx.config.get("with_watch_providers", "")))
        return BuilderResult(
            ids=[("tmdb", value) for value in ids],
            poster_kind="streaming" if service else None,
            poster_key=service,
        )
```

- [ ] **Step 4: Wire the resolution buckets**

In `src/autoposter/collections/builders/plex_trivial.py`, add `from autoposter.collections.default_images import RESOLUTION_KEYS` at the top and replace `PlexAllBuilder.build`'s return:

```python
        rating_keys = list(access.owned_index()["plex"])
        logger.debug("plex_all: %d owned item(s)", len(rating_keys))
        # ``media_resolution`` is four definitions of THIS builder distinguished
        # only by their ``resolution`` filter, so the filter is where the
        # family identity lives and there is nowhere else to read it from. The
        # first value is the bucket's own key -- ``("4k", "8k")`` is "4k, with
        # 8k folded in", upstream's own addon merge -- and it is one of the
        # four labels ``Default-Images/resolution/`` names its files by
        # (``.superpowers/sdd/p-defimg-probe.md`` §5). Any other filter, and a
        # bare ``plex_all``, keep no default artwork: this builder is "every
        # item the library owns", which upstream has no picture of.
        values = (getattr(ctx.definition, "filters", None) or {}).get("resolution")
        bucket = values[0] if isinstance(values, list) and values else None
        matched = bucket if bucket in RESOLUTION_KEYS else None
        return BuilderResult(
            ids=[("plex", key) for key in rating_keys],
            poster_kind="resolution" if matched else None,
            poster_key=matched,
        )
```

- [ ] **Step 5: Wire the three universe list builders**

In `src/autoposter/collections/builders/imdb_lists.py`, add `from autoposter.collections.default_images import UNIVERSE_CODES` at the top and replace `ImdbListBuilder.build`'s return:

```python
    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = ImdbListParams.model_validate(ctx.config)
        entries = await fetch_list(ctx.http, params.list)
        # Eight of Kometa's universes are public IMDb lists whose ids this
        # catalog transcribes (``catalog._UNIVERSE_LISTS``), and upstream names
        # its universe art by a short code rather than a display name
        # (``.superpowers/sdd/p-defimg-probe.md`` §5/§6) -- so the list id is
        # both the only thing on this generic builder that names the universe
        # and the only stable join to that code. Every other IMDb list
        # definition, of which there are many, is not in the table and keeps no
        # default artwork.
        code = UNIVERSE_CODES.get(params.list)
        return BuilderResult(
            ids=_library_owned(entries, ctx, "IMDb list %r" % params.list),
            poster_kind="universe" if code else None,
            poster_key=code,
        )
```

In `src/autoposter/collections/builders/mdblist.py`, add the same import and replace the final `return BuilderResult(ids=ids)`:

```python
        # The DC split's two MDBList universes join the same table by their
        # list ref -- see ``ImdbListBuilder`` for why the ref rather than the
        # title. 'In Association With DC' is deliberately absent from that
        # table: upstream's ``dca`` is DC ANIMATED, a different continuity, and
        # lending it that art would be a plausible wrong poster rather than an
        # absent one.
        code = UNIVERSE_CODES.get(params.list)
        return BuilderResult(
            ids=ids,
            poster_kind="universe" if code else None,
            poster_key=code,
        )
```

In `src/autoposter/collections/builders/tmdb.py`, `TmdbListBuilder.build` — the DC Universe collection is a TMDb list:

```python
    async def build(self, ctx: BuilderContext) -> BuilderResult:
        params = TmdbEntityParams.model_validate(ctx.config)
        require_library_type(
            f"the {self.type_name!r} builder", ctx.library_type, self.media_types
        )
        client = self._client(ctx)
        ids = await client.list_items(
            params.id, media_type=self.media_types[ctx.library_type]
        )
        # One TMDb list is a universe -- the DC split's 'DC Universe', list
        # 8642250 (``catalog._DC_LISTS``) -- and it joins the universe table by
        # the same list ref the IMDb and MDBList universes do. The id is
        # stringified because the table's keys are refs as written, and MDBList
        # refs are not numbers.
        code = UNIVERSE_CODES.get(str(params.id))
        return BuilderResult(
            ids=[("tmdb", value) for value in ids],
            poster_kind="universe" if code else None,
            poster_key=code,
        )
```

Add `from autoposter.collections.default_images import UNIVERSE_CODES` to `builders/tmdb.py`'s imports. This module now imports `default_images` at module scope, which is safe: `default_images` imports only `posters`, and `posters` imports no builder.

- [ ] **Step 6: Run the wiring tests and verify they pass**

```bash
docker compose -p pdi3 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run --rm test sh -c "pytest -q tests/test_collection_poster_wiring.py tests/test_collection_default_images.py 2>&1 | tee /app/.superpowers/run-pdi3-green.log"
docker compose -p pdi3 down
tail -20 .superpowers/run-pdi3-green.log
```

Expected: `0 failed`.

- [ ] **Step 7: Full suite, golden gate, and commit**

```bash
docker compose -p pdi3b -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pdi3b-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pdi3-full.log"
docker wait pdi3b-full
tail -10 .superpowers/run-pdi3-full.log
docker compose -p pdi3b down
git status --porcelain tests/fixtures/collections/golden_port.json
```

Expected: `0 failed`; the golden fixture line prints NOTHING. A failure in an existing `tmdb_discover`, `plex_all`, `imdb_list`, `tmdb_list` or `mdblist_list` test is the signal that one of those builders' EXISTING contract moved — investigate with `superpowers:systematic-debugging`; these five builders are used by dozens of definitions and only the two new fields may change.

**Stated delta:** T2's measured total + 7 tests (3 in `test_collection_default_images.py`, 4 in `test_collection_poster_wiring.py`).

```bash
git add src/autoposter/collections/builders/tmdb_discover.py \
        src/autoposter/collections/builders/plex_trivial.py \
        src/autoposter/collections/builders/imdb_lists.py \
        src/autoposter/collections/builders/mdblist.py \
        src/autoposter/collections/builders/tmdb.py \
        tests/test_collection_default_images.py \
        tests/test_collection_poster_wiring.py
git commit --no-gpg-sign -m "feat(collections): streaming, resolution and universe posters from Default-Images"
```

---

### Task 4: Wrap — the roadmap row, the PR body, the whole-branch verification

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`
- Create (gitignored, uncommitted): `.superpowers/sdd/p-defimg-pr-body.md`

**Interfaces:** none — this task writes no code and changes no behaviour.

- [ ] **Step 1: Confirm no row already covers the directive**

```bash
grep -n "Default-Images" docs/superpowers/specs/2026-08-22-full-parity-roadmap.md | cut -c1-160
grep -n "^| 2[12][0-9] " docs/superpowers/specs/2026-08-22-full-parity-roadmap.md | tail -3
```

Expected: the `Default-Images` hits are rows 83 (people art ABSENT upstream) and 211 (dividers/separators) — neither is the per-member poster directive, which confirms the facts file's C3 suspicion that it was never filed. The last row number printed is the one this phase's row follows. **If a per-member poster row DOES exist, do not file a second one** — update that row in place and say so in the PR body.

- [ ] **Step 2: File the row, filed-and-closed by this phase**

Append one row to the roadmap table, numbered one past the last (the check above prints it; `220` if the tail is `219`):

```markdown
| 220 | Managed collections carry no upstream art beyond awards, charts and content ratings — every enumerated family is postered by hand or not at all (CLOSED — the default-images-posters phase) | Filed and closed by the same phase (`feat/default-images-posters`), on an operator directive: managed collections should pull poster art from `Kometa-Team/Default-Images` **where a matching asset exists**, falling back to today's behaviour where none does. **What was actually missing was not machinery.** The poster pipeline has shipped since phase 4 — three sources in priority order through one `apply_poster`, hash-compared so an unchanged pass uploads nothing, and gated globally by `collections.posters` — and it already answered six kinds. What it did not have was (a) a table of which upstream directory each ENUMERATED family's art lives in, and (b) any route from a family builder to that table. Both are now one module, `collections/default_images.py`, whose table is DATA: every directory, key scheme and code in it is transcribed from live GitHub Contents API listings recorded in `.superpowers/sdd/p-defimg-probe.md` §1–§6, never recalled. **Twelve families wired, three routes.** Franchises through `TmdbCollectionBuilder`'s existing `BuilderResult.poster_kind` seam; nine dynamic families (genre, studio, country, network, decade, year, resolution, audio_language, subtitle_language) through a new `DynamicType.poster_kind` column threaded into `reconcile_smart_collection`, whose `apply_poster` call had hardcoded `kind=None, key=None` for every dynamic family since 9c; and three families whose builders are generic (`production_streaming` on `tmdb_discover`, `media_resolution` on `plex_all`, `content_universes`/`content_dc` on `imdb_list`/`tmdb_list`/`mdblist_list`) through the one field on each definition that names the family — the watch-provider id, the `resolution` filter, the list ref. **The naming finding that shaped all of it:** upstream keys per-member art by NAME (`franchise/Jurassic Park.jpg`), never by id — the probe found no TMDb-collection-id naming anywhere in the repository — which is why the franchise key is the collection's own title and not `params.id`, and why the universe and streaming families need explicit ref→code and id→name tables rather than a derivation. **Four families in the directive's scope are OUT because we do not build them:** `aspect`, `year` and `seasonal` are GATED presets that build nothing, and `video_format` has no catalog row at all; `year` and `resolution` are nevertheless filled as dynamic-type rows an operator can write by hand, so those packs flipping READY later costs nothing. `content_rating` gets no dynamic poster column deliberately: upstream's is region-scoped and a dynamic definition names no region, so choosing one would resolve to another country's rating artwork. **Fallback is the common case and it is silent** — upstream curates 116 franchises against TMDb's entire collection space — so a miss is one DEBUG line and today's behaviour, never a WARNING, and it is cached as a marker so a library of unmatched franchises does not re-prove the same absence every pass. **Priority is unchanged and pinned:** the operator's local file is read first, unconditionally, before `kind`/`key` is ever consulted, so this can only fill gaps. **No new config surface:** `collections.posters` still governs, there is no new toggle and no new UI, and `hosted_poster_url`'s six-kind table was not extended — the cached family poster rides the `generated=` rank `separator_art` already occupies, which is what keeps the local-override guarantee structural rather than re-argued. **Licence unchanged and not re-litigated:** `Default-Images` carries no LICENSE deliberately, per the maintainer on Discord 2026-08-29 — *"nearly all the default images are based on other work, so I'm not sure it's reasonable or valid to apply a license to derivative works"* — images are fetched at runtime and not vendored, the same posture `collections/posters.py` and `assets/fonts/PROVENANCE.md` already record. Adjudications A1–A8 in the plan, C1–C4 in `.superpowers/sdd/p-defimg-facts.md` | M — one new module, one branch in `apply_poster`, one table column, one signature threaded, five builders each gaining two fields | yes — twelve collection families gain upstream artwork where it exists, with no setting to change | 211 (closed — the divider art this is the per-member sibling of), 83 (closed — records that upstream has NO people folder, which is why people are not in this), 199 (open — franchise `minimum_items`, untouched) |
```

If any family was left unwired because T1's probe could not confirm its code, say so IN THE ROW with the family named — an honest gap in a closed row beats a row that claims more than shipped.

- [ ] **Step 3: Commit the roadmap**

```bash
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit --no-gpg-sign -m "docs(roadmap): row 220 filed and closed — Default-Images family posters"
```

- [ ] **Step 4: Write the PR body (gitignored, NOT committed)**

Write `.superpowers/sdd/p-defimg-pr-body.md`. Title: `feat: Default-Images posters for every collection family we build`. Sections:

- **What** — one paragraph per task: the mapping module + cache + franchises; the dynamic column and the `None, None` that it replaced; the three generic-builder families; the paperwork.
- **Why** — the directive in one sentence, then the recon's finding (the machinery existed; the mapping and the route did not) and the probe's finding (name-keyed, never id-keyed).
- **Evidence** — the probe file's §1–§6, the four listings T1 captured live and when, the measured suite numbers per task against T1's baseline, the golden gate held in T1/T2/T3.
- **Behaviour an operator can see** (screenshot-friendly, and the reason this section exists) — twelve families gain artwork on the next pass; the report line reads `set the poster for 'X' from the hosted default image`; a family with no upstream match reads `no poster source for 'X'` exactly as before; a hand-placed `poster.jpg` still wins and is never fetched over; nothing to switch on, `collections.posters` already governs.
- **Not done here** — `aspect`/`year`/`seasonal`/`video_format` (we build nothing to poster); logos, overlays, white and best/standards variants (overlay-phase material); `based`/`playlist` (out by C1.6); any family whose upstream code T1 could not confirm, named.

No AI attribution anywhere in it. `.superpowers/` is gitignored; this file stays uncommitted.

- [ ] **Step 5: Whole-branch verification**

```bash
docker compose -p pdi4 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pdi4-full test sh -c "pytest -q 2>&1 | tee /app/.superpowers/run-pdi4-full.log"
docker wait pdi4-full
tail -15 .superpowers/run-pdi4-full.log
docker compose -p pdi4 down
```

Expected: `0 failed`, with a total matching T1's baseline + 31 (19 + 5 + 7) and one new test file. **Investigate any failure with `superpowers:systematic-debugging` before ANY completion claim — evidence before assertions.** State both the expected and the measured totals in the report; if they differ, the difference is explained, not rounded away.

- [ ] **Step 6: Confirm the branch is clean and parked**

```bash
git status --porcelain
git log --oneline origin/main..HEAD
grep -rn "Co-Authored-By\|Generated with\|Claude" $(git log --format=%H origin/main..HEAD | head -1) 2>/dev/null || true
git log origin/main..HEAD --format='%an <%ae>%n%B%n---'
```

Expected: `git status` prints nothing (the PR body lives under gitignored `.superpowers/`); five commits — the plan, T1, T2, T3, the roadmap; no AI attribution of any kind in any message. **No push, no PR** — the controller does both after the whole-branch review.

---

## Self-Review

**1. Spec coverage.** Facts C1.1 (scope): every in-scope family is either wired (franchise T1; genre/studio/country/network/decade/year/resolution/audio_language/subtitle_language T2; streaming/resolution/universe T3) or explicitly OUT with the catalog evidence (A2: aspect/year/seasonal GATED, video_format absent). C1.2 (one mapping module with the probe's table as data): `default_images.py`, T1 Step 7, with the universe/streaming/resolution code tables and the documented strip-variants. C1.3 (fetch/cache under `.generated/collection-posters/<family>/`, raw URL with percent-encoding, graceful 404, base asset only): T1 Steps 7 and 5, Constraint 2, adjudication A1. C1.4 (three hook shapes): T1 Step 15, T2 Steps 3/7/8, T3 Steps 3/4/5. C1.5 (existing switch, local override wins, licence in the docstring): Constraints 3 and 4, T1 Step 5's priority test and Step 9's, the module docstring's licence paragraph. C1.6 (logos/overlays/styles, based/playlist, new UI all OUT): Constraint 2 and its `test_every_family_row_names_a_real_directory_shape` guard; no catalog row for based/playlist is touched; no frontend file is in the File Structure. C2 (the law): per-key-scheme tests citing the probe by section, MockTransport only, the 404 fallback proven twice (module level and `apply_poster` level), priority pinned, stated-then-measured from a T1 baseline. C3 (paperwork): T4 Steps 1–4, one row filed and closed, PR body plain and screenshot-friendly. C4 (4 tasks, branch, artifacts, no push): the four tasks, Constraint 6, Constraint 10.

**2. Placeholder scan.** No TBD, no "handle edge cases", no "similar to Task N". Two deliberate fill-in points remain and both are *data captures with an explicit source and a STOP*: T1 Step 4 pastes the probe's verbatim output into §6, and T1 Step 7 completes four `UNIVERSE_CODES` rows from that §6 — neither may be guessed, both name what to do if the capture fails, and T3 re-checks them in its CONFIRMED-BY-T1 block. T2 Step 8 offered two forms of one line and the plan picks one explicitly.

**3. Type consistency.** `default_images.FAMILIES` / `candidate_urls` / `default_image_url` / `ensure_default_image` / `UNIVERSE_CODES` / `STREAMING_NAMES` / `RESOLUTION_KEYS` are spelled identically in T1's Interfaces block, T1's implementation, T2's test, and T3's four builders. `Family(directory, key_scheme, variants, note)` is constructed positionally in T1 Step 7 and read as `.directory` in the T1 guard test — consistent. `DynamicType.poster_kind` is the same name in the dataclass, the ten rows, `poster_for_unit`, and both T2 tests. `reconcile_smart_collection`'s two new parameters are `poster_kind`/`poster_key` at the definition (T2 Step 7), at the call site (T2 Step 8) and in the test (T2 Step 5) — deliberately NOT `kind`/`key`, which are `apply_poster`'s parameter names one layer down and would read as a passthrough of something this function already had. `BuilderResult.poster_kind`/`poster_key` are the existing field names, unchanged, in all five builders.
