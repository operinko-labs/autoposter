# Plex batched read — the live read-only probe

Phase B. Run against the operator's production Plex on 2026-08-30,
**read-only throughout** — every request is a GET, nothing was written, no
Secret was read through the Kubernetes API. The server address and the token
are scrubbed from every line here and from the captured output; the
placeholders are `<plex-url>`, `<plex-host>` and `<token>`. The scripts contain
no literal URL and no literal token — the URL arrives from the pod's own
operator config (`load_config(DEFAULT_CONFIG_PATH).plex.url`) and the token
from `Secrets.from_env().plex_token`, both for the duration of the run and gone
with it. The five scripts were deleted after their output was captured; §6
reproduces them verbatim, and they are the authority for what was sent.

**Server:** the same production instance 9b's and 10a's probes ran against
(`docs/research/plex-search-probe/README.md`,
`docs/research/plex-dynamic-probe/README.md`). Sections used are the ones
`section_of()` selects — the first non-DVR `movie` section (**1962** items
today, 1960 at 9b's run) and the first non-DVR `show` section (**286** items).
Probes ran in the `autoposter` pod in namespace `media` via
`kubectl exec -n media -i deploy/autoposter -- python - < script.py`.

**What it exists to decide.** Phase B's tasks 2–7 are written against seven
measurements they cannot make for themselves. The gate that mattered most is
D1: do Role/Director/Writer/Producer tags ride the batched
`/library/metadata/{k1,k2,…}` read at all — if they don't, tasks 4 and 5 have
no credits source. 9a's tag counter never enumerated the credit families (it
counted only Genre/Collection/Country/Label/Stream); this probe is the first
measurement of them, not a reversal of an earlier one. §7 records all seven
verdicts.

---

## 1. Probe a — the batched read on the SHOW library

Never measured before (9b and 10a both worked the movie section). Chunk sizes
50/100/200 against the 286-show library, counting every tag family the phase
cares about in the returned XML, then one movie's streams sampled for stream
language attribute spellings (D6) — the script fetches five movie keys but the
loop breaks after the first item's streams are read (§6.1).

```
show rating keys: 286
chunk= 50 returned= 50   0.47s tags={'Genre': 114, 'Label': 47, 'Collection': 34, 'Country': 53, 'Role': 5457, 'Director': 0, 'Writer': 0, 'Producer': 0, 'Stream': 0}
chunk=100 returned=100   0.82s tags={'Genre': 228, 'Label': 95, 'Collection': 63, 'Country': 106, 'Role': 10569, 'Director': 0, 'Writer': 0, 'Producer': 0, 'Stream': 0}
chunk=200 returned=200   2.41s tags={'Genre': 475, 'Label': 190, 'Collection': 117, 'Country': 207, 'Role': 20207, 'Director': 0, 'Writer': 0, 'Producer': 0, 'Stream': 0}
  stream type=2 language='English' languageCode='eng' languageTag='en'
  stream type=2 language='English' languageCode='eng' languageTag='en'
  stream type=3 language='English' languageCode='eng' languageTag='en'
```

**Reading it.** Every requested key came back — 50/50, 100/100, 200/200, no
silent truncation of the batch itself. `Role` rides the show batch in volume.
`Director`/`Writer`/`Producer` are **zero on shows**, and §2 shows this is a
property of Plex's show-level metadata rather than of the batched read: a
single-key fetch of the same shows returns exactly the same credit families
(MATCH=True), and `listFilterChoices` on the show section answers *0 values*
for director/writer/producer while answering 789 for actor. Series-level shows
on this server simply carry no director/writer/producer tags. `Stream` is zero
on shows as the probe's own comment predicted — shows carry no `Media`, which
is why the D6 sample is taken from movies.

**Cost.** 9.4 ms/item at chunk 50, 8.2 at chunk 100, 12.05 at chunk 200. The
per-item cost is not flat, and §3 shows it climbing steeply beyond 200.

## 2. Probe b — do credit tags ride the batch at all? (the D1 gate)

A 200-key batch on each of the movie and show sections; totals per credit
family; the per-item `Role` histogram; then a single-key fetch of three items
per section compared against those same items as they arrived in the batch;
then `listFilterChoices` for the four people fields (D7).

```
movie batch of 200 credit tag totals: {'Role': 8815, 'Director': 228, 'Writer': 482, 'Producer': 590}
movie Role-per-item histogram: {3: 1, 5: 2, 6: 1, 7: 3, 8: 1, 9: 4, 11: 2, 12: 5, 13: 3, 14: 5, 15: 6, 16: 9, 17: 5, 18: 4, 19: 6, 20: 3, 21: 4, 22: 2, 23: 2, 24: 5, 25: 6, 26: 1, 27: 4, 28: 1, 29: 4, 30: 2, 31: 3, 32: 1, 33: 2, 34: 1, 36: 1, 37: 3, 38: 1, 39: 5, 40: 3, 41: 4, 42: 1, 43: 3, 44: 1, 45: 3, 47: 5, 48: 3, 49: 3, 50: 3, 51: 2, 52: 3, 53: 6, 55: 3, 56: 2, 57: 1, 58: 1, 59: 1, 60: 1, 61: 2, 64: 2, 65: 1, 68: 1, 69: 2, 70: 4, 71: 1, 72: 1, 75: 3, 76: 1, 77: 1, 83: 3, 85: 1, 86: 1, 89: 1, 92: 1, 95: 1, 101: 1, 103: 1, 104: 1, 110: 4, 112: 1, 135: 2, 139: 1, 157: 1, 166: 1, 181: 1, 200: 2}
  key=158244 MATCH=True single={'Director': 1, 'Writer': 3, 'Role': 31, 'Producer': 1}
  key=164890 MATCH=True single={'Director': 1, 'Writer': 4, 'Role': 39, 'Producer': 4}
  key=65692 MATCH=True single={'Director': 1, 'Writer': 5, 'Role': 12, 'Producer': 8}
  listFilterChoices movie/actor: 3268 values
  listFilterChoices movie/director: 1394 values
  listFilterChoices movie/writer: 2813 values
  listFilterChoices movie/producer: 3071 values
show batch of 200 credit tag totals: {'Role': 20207, 'Director': 0, 'Writer': 0, 'Producer': 0}
show Role-per-item histogram: {1: 5, 2: 4, 3: 1, 4: 2, 5: 4, 6: 1, 7: 1, 8: 2, 9: 2, 11: 1, 12: 4, 13: 1, 14: 2, 15: 2, 16: 3, 17: 1, 18: 2, 20: 1, 23: 1, 24: 2, 25: 1, 26: 2, 27: 1, 28: 3, 29: 2, 30: 1, 31: 1, 32: 2, 33: 1, 34: 3, 36: 2, 38: 1, 39: 1, 40: 2, 41: 1, 42: 1, 45: 2, 46: 1, 47: 1, 48: 1, 49: 3, 53: 1, 56: 1, 58: 1, 59: 2, 60: 1, 62: 1, 64: 1, 65: 3, 66: 1, 67: 1, 68: 3, 69: 2, 71: 1, 72: 2, 74: 1, 79: 1, 85: 1, 86: 3, 87: 1, 88: 1, 90: 1, 92: 1, 98: 1, 100: 1, 103: 1, 104: 2, 109: 2, 111: 1, 112: 2, 113: 1, 117: 1, 118: 2, 122: 1, 125: 1, 128: 1, 136: 3, 144: 1, 149: 1, 150: 1, 151: 1, 155: 1, 156: 1, 158: 1, 159: 3, 160: 1, 162: 1, 165: 1, 170: 1, 172: 1, 173: 1, 176: 1, 177: 1, 191: 1, 192: 1, 200: 54}
  key=84951 MATCH=True single={'Role': 200}
  key=83713 MATCH=True single={'Role': 46}
  key=78460 MATCH=True single={'Role': 98}
  listFilterChoices show/actor: 789 values
  listFilterChoices show/director: 0 values
  listFilterChoices show/writer: 0 values
  listFilterChoices show/producer: 0 values
```

**D1 is YES.** Role, Director, Writer and Producer all ride the batched
`/library/metadata/{k1,k2,…}` response on the movie section — 8815 roles, 228
directors, 482 writers, 590 producers across 200 movies. Tasks 4 and 5 are
**not** blocked, and no adjudication text is written below the table.

**The equivalence check is the load-bearing part.** For all six sampled items
(three movies, three shows) `MATCH=True` — the credit families in the batched
item are *identical, tag-for-tag and name-for-name*, to the same item fetched
by itself. The batched read is not a lossy read. The comparison is on the tag
name lists, not merely on counts.

**Show credits are Role-only.** Zero director/writer/producer on shows in the
batch, zero in the single-key fetch, and zero enumerable values from
`listFilterChoices`. Three independent readings agree, so this is a fact about
series-level Plex metadata (those credits live on episodes), not a batch
artefact. **The credit resolvers must not expect show-level director/writer/producer.**

**A 200-cap on `Role` children.** 54 of 200 shows and 2 of 200 movies land on
*exactly* 200 roles, and none exceed it — a server-side cap on how many `Role`
children one item's metadata returns, not a coincidence of cast sizes. The
single-key fetch of key=84951 also returns exactly 200, so the cap is not
imposed by batching and cannot be escaped by dropping to single-key reads. Any
credit-resolver claim of a *complete* cast is false for the ~27% of shows that hit it.

## 3. Probe c — the URL-length chunk cap

Escalating chunks on the 1962-key movie section, printing the path length
before each attempt, stopping at the first refusal.

```
movie rating keys: 1962
chunk= 400 path-length= 2547 ...
  OK returned= 400   6.20s
chunk= 800 path-length= 5115 ...
  OK returned= 800  20.13s
chunk=1200 path-length= 7671 ...
  OK returned=1200  44.05s
chunk=1600 path-length=10232 ...
  REFUSED BadRequest status=None
```

**Reading it.** The largest chunk that worked is **1200 keys / 7671 path
characters**; **1600 keys / 10232 characters** is refused as
`plexapi.exceptions.BadRequest`, with no `status_code` reachable on the
exception (plexapi raises `BadRequest` from the status line without attaching
the response, so callers cannot branch on the code — callers must catch the
exception class, not a status). The boundary lies somewhere in 1200 < cap <
1600; the probe stops at the first refusal by design, so the exact cap is not
measured. The shape is consistent with the classic 8192-byte request-line
limit: the 7671-character path plus scheme, host and the token query parameter
sits just under it, and the 10232-character path is far over.

**The cap is not the binding constraint — cost is.** Per-item time climbs with
chunk size: 15.5 ms/item at 400, 25.2 at 800, 36.7 at 1200, against 8–12
ms/item at 50–200 in §1. The 400-key point sits against a governing prior: 9a
measured the same movie section at chunk=400 in 3.25 s (8.1 ms/item; archive
`p9a-task-2-report.md:319-323`) — a 1.9× divergence from this run's 6.20 s
(15.5 ms/item) at the one directly comparable point, same server, same
section, overlapping key range. The variance is unexplained (different day,
load, or item set) and is named here rather than left standing as an
unreconciled contradiction. The 800/1200 points are far beyond anything 9a
measured, so the climb from 400→800→1200 is this run's own and unaffected by
the discrepancy at 400: a 1200-key chunk takes 44 s for one request; six
200-key chunks covering the same 1200 items would cost roughly 14 s.
**Chunking large is slower, not faster, above 400 keys.** The plan's
`TAG_BATCH_CHUNK` of 200 needs no reduction (the trigger was "< 200") either
way — it is also near the cost optimum under both this run's and 9a's
numbers.

## 4. Probe d — do viewCount/lastViewedAt/userRating reach the section listing?

One raw `/library/sections/{key}/all` GET per section, counting attribute
presence over every returned row.

```
movie items=1962 viewCount-present=79 (of which =0: 0) lastViewedAt-present=98 userRating-present=2 credit-children-in-listing=11235
show items=286 viewCount-present=55 (of which =0: 0) lastViewedAt-present=64 userRating-present=0 credit-children-in-listing=844
```

**Reading it: SPARSE, and sparse in a specific, usable way.** The attributes do
reach the listing, but only on the rows that have a value. `viewCount` is
present on 79/1962 movies and 55/286 shows and is **never `0`** — Plex omits
the attribute entirely for unwatched items rather than writing a zero. An
absent `viewCount` therefore means *unwatched*, and rows 180/175 can read
`int(attrib.get("viewCount", 0))` off the listing without a per-item fetch.

`lastViewedAt` is present more often than `viewCount` (98 vs 79 on movies, 64
vs 55 on shows) — the excess is in-progress items, which carry a last-viewed
timestamp before they ever complete a play. Code must not infer one from the
other.

`userRating` is effectively **ABSENT** as a usable signal on this library: 2 of
1962 movies, 0 of 286 shows. It is present-when-set like the others, so the
accessor is sound, but any feature keyed on it will find almost no data
here.

**Informational.** Credit tags also appear as children *in the section
listing*: 11235 across 1962 movies (≈5.7/item) and 844 across 286 shows
(≈3/item). Far fewer than the batched metadata read returns (8815 credits per
*200* movies in §2), so the listing carries an abbreviated set. The probe
counts the four families collectively and does not break them down; anything
that wants to rely on listing-borne credits must measure the breakdown first.

## 5. Probe e — `totalSize` at container-size 0

```
container-size-0: totalSize='17' size='0' children=0
full fetch of same URL: 17 item(s)
VERDICT: CONFIRMED
```

**Reading it.** `X-Plex-Container-Start: 0` with `X-Plex-Container-Size: 0`
returns `totalSize="17"` while shipping **zero** child elements (`size="0"`,
0 children), and the full fetch of the identical search URL returns exactly 17
items. Row 198's count-without-payload idea works: a filtered result set can be
counted in one tiny GET.

**Unmeasured, and named here rather than assumed:** this probe used a filter
matching 17 items, so whether Plex still emits `totalSize` (as `"0"`) on a
container whose result set is genuinely EMPTY was never measured. If it omits
the attrib there, `smart.count_matches` raises `SmartCollectionUnavailable`
("no totalSize attrib") instead of returning 0 — the same refusal
`require_matches` would give anyway, but under the unreadable-count message
rather than the matched-nothing one.

---

## 6. The scripts, verbatim

Each of the five scripts on disk was exactly the shared preamble below,
followed by that probe's body. They were run as
`kubectl exec -n media -i deploy/autoposter -- python - < probe_X.py` and
deleted after their output was captured (§8).

### 6.0 The shared preamble (identical at the top of all five)

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

### 6.1 `probe_a.py` body

```python
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

# stream language attribs, for the accessor spelling (D6): sample the first
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

### 6.2 `probe_b.py` body

```python
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

### 6.3 `probe_c.py` body

```python
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

### 6.4 `probe_d.py` body

```python
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

### 6.5 `probe_e.py` body

```python
movie = section_of("movie")
path = "/library/sections/%s/all?type=1&year=1994" % movie.key
data = server.query(path, headers={"X-Plex-Container-Start": "0", "X-Plex-Container-Size": "0"})
say("container-size-0: totalSize=%r size=%r children=%d"
    % (data.attrib.get("totalSize"), data.attrib.get("size"), len(list(data))))
actual = len(movie.fetchItems(path))
say("full fetch of same URL:", actual, "item(s)")
say("VERDICT:", "CONFIRMED" if str(actual) == data.attrib.get("totalSize") else "NOT CONFIRMED")
```

---

## 7. Decisions (the gate for plan tasks 4-7)

| # | Question | Verdict | Gates |
| --- | --- | --- | --- |
| D1 | Do Role/Director/Writer/Producer tags ride the batch response (probe b)? | **YES** — movie batch of 200 carried Role 8815, Director 228, Writer 482, Producer 590; batch vs single-key credit lists identical on 6/6 sampled items (MATCH=True). Shows carry **Role only** (0 director/writer/producer in batch, in single-key, and in `listFilterChoices`) — a property of series-level Plex metadata, not of batching. **`Role` children are capped at 200/item server-side** (54/200 shows and 2/200 movies hit it exactly, unescapable by single-key reads) — counts from a truncated cast must not claim completeness. | The credit resolvers **proceed**. No adjudication. |
| D2 | Largest working chunk / refusal shape (probe c) | Largest tested OK: **1200 keys / 7671-char path (44.05 s)**. **1600 keys / 10232 chars REFUSED, `BadRequest`, `status=None`** (plexapi attaches no response, so catch the class, not a code). Exact cap unmeasured: 1200 < cap < 1600. Cost, not the cap, binds: 15.5 → 25.2 → 36.7 ms/item at 400/800/1200 vs 8–12 ms/item at 50–200. | `TAG_BATCH_CHUNK` **unchanged at 200** (trigger was "< 200"); 200 is also near the cost optimum. |
| D3 | Show-library batch economics (probe a) | 286 shows: **2 calls at chunk=200**; measured 2.41 s for the 200-chunk and 0.82 s for a 100-chunk, so ≈**3.2 s** for the whole show library, ≈**11 ms/item**. Per-chunk measured: 50 → 0.47 s (9.4 ms/item), 100 → 0.82 s (8.2), 200 → 2.41 s (12.05). | recorded; informs nothing structural |
| D4 | `viewCount`/`lastViewedAt`/`userRating` in the listing (probe d) | **SPARSE, present-when-set** — `viewCount` movie 79/1962, show 55/286, **never `0`** (absent ⇒ unwatched); `lastViewedAt` movie 98/1962, show 64/286 (exceeds `viewCount` — in-progress items, do not infer one from the other); `userRating` movie 2/1962, show 0/286 — **effectively ABSENT** on this library. | **This verdict supersedes the binary ALL-PRESENT/SPARSE branch first planned** — the three attributes split rather than moving together, so the tier is decided per-attribute instead of by picking one branch on a keyword match. `plays` **SHIPS listing-tier**: plexapi already defaults `viewCount` to 0 (`video.py:64`, `utils.cast(int, data.attrib.get('viewCount', 0))`), so absent-is-zero is safe regardless of accessor mechanism — add to `_LISTING_ATTRIBS`. `last_played` is **DECIDED LATER** against Kometa's own None handling: plexapi leaves `lastViewedAt` None when absent (`video.py:50`), so a never-played item reads as MISSING and a recency filter's missing-excludes rule would exclude it unconditionally — it ships listing-tier only if missing-excludes is the wanted semantics for `last_played`, else re-files that half of row 180 honestly (not closed). `user_rating` **ships** per the plan's sparse-ships resolution (`:2471`) — missing-excludes is the correct semantics for an unrated item. |
| D5 | `totalSize` at container-size 0 (probe e) | **CONFIRMED** — `totalSize='17'`, `size='0'`, 0 children; full fetch of the same URL returned 17. | the row-198 commit |
| D6 | Stream `language` attrib spelling (probe a) | **All three carry values** on audio (`streamType=2`) and subtitle (`streamType=3`) streams: `language='English'`, `languageCode='eng'`, `languageTag='en'`. Sample is one movie's streams, all English. | the `_stream_languages` field — `languageCode` (ISO 639-2) is the safe key; `languageTag` (ISO 639-1) matches 9b's `_base_language_code` shape. **Shipped: `languageTag`** — see `client._stream_languages`, which argues the normaliser match makes the comparison an identity for the common case. |
| D7 | `listFilterChoices` enumerates actor/director/writer/producer (probe b) | **movie: all four answer** — actor 3268, director 1394, writer 2813, producer 3071. **show: actor 789 only** — director/writer/producer answer **0 values** (no refusal, an empty enumeration). | The resolver: sufficient for movies and for show *actors*; a show director/writer/producer resolver has nothing to enumerate (the hubSearch fallback is the only route if that is ever needed). |

---

## 8. Key-grep and clean-up

Run before the commit, over this file. The scrub greps are local; the
key-material grep runs **inside the pod**, where the real host and token are
available, so that neither ever reaches an operator terminal, this repo, or
pod disk — it is a single `grep -F` pass over this file streamed on stdin, with
a positive control proving the same grep does fire when the material is
present.

```
$ grep -Eo 'https?://[^ )"]+' docs/research/plex-batch-probe/README.md | sort -u
(no output)

$ grep -c '<plex-host>\|<plex-url>' docs/research/plex-batch-probe/README.md
3

$ kubectl exec -n media -i deploy/autoposter -- sh -c '
    HOST=$(python -c "from urllib.parse import urlsplit; from autoposter.config.loader import DEFAULT_CONFIG_PATH, load_config; print(urlsplit(load_config(DEFAULT_CONFIG_PATH).plex.url).netloc)")
    TOKEN=$(python -c "from autoposter.config.schema import Secrets; print(Secrets.from_env().plex_token)")
    echo "report host-or-token hits: $(grep -F -c -e "$HOST" -e "$TOKEN" || true)"
    echo "positive control hits: $(printf "x%sy%sz\n" "$HOST" "$TOKEN" | grep -F -c -e "$HOST" -e "$TOKEN" || true)"
    echo "negative control hits: $(printf "no key material here\n" | grep -F -c -e "$HOST" -e "$TOKEN" || true)"
  ' < docs/research/plex-batch-probe/README.md
report host-or-token hits: 0
positive control hits: 1
negative control hits: 0
```

Zero hits in this file; the positive control shows the grep is live rather than
vacuously clean. The five scripts and their `.out` captures were then deleted:

```
rm probe_a.py probe_b.py probe_c.py probe_d.py probe_e.py probe_*.out
```
