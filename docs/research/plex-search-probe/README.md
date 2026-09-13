# Plex search DSL — the live read-only probe

Phase 9b. Run against the operator's production Plex on 2026-08-26 with
explicit go-ahead, **read-only throughout**. The server address and token are
scrubbed from every line here and in `probe-roundtrip.txt`; the placeholder is
`<plex-host>`.

**Server:** Plex Media Server `1.43.4.10903-e5521bd8c`, Linux 20.04.6 LTS.
plexapi 4.18.2. Sections: 1 `Movies` (movie, **1960** items), 2 `TV Shows`
(show, **286** items), 3 `Photos`, 5 `DVR`.

Raw output: [`probe-roundtrip.txt`](probe-roundtrip.txt), 185 lines, exactly as
the script printed them save for one edit recorded here: the capture was taken
through `sh -c 'ruff check … && python p9b_probe.py'`, so ruff's own
`All checks passed!` landed on line 1 of the redirect. That single line was
removed so the file's content matches the command documented in §8 as
generating it. Nothing the probe itself emitted was altered.

The script is reproduced verbatim in §8 — it is the authority for what was
sent.

---

## 0. Headline

| | Verdict |
| --- | --- |
| **Part A** — nine dual-path round-trips | **9/9 AGREE, zero divergence.** No translation bug. |
| **Probe 1** — `show.network` as a search field | **ANSWERS — 91 networks.** `production_network` **UNBLOCKS** (end-to-end on one network's two shows — see §3). |
| **Probe 2** — language expansion under `all:` | **Confirmed unusable under `all:`** — 0 vs 442. File the row. |
| **Probe 3** — genre search vs listing truncation | **5/5.** The search path is not truncated. |
| **Part C** — the six removed spellings | **Plex answers all six.** The narrowing is a Kometa inheritance by choice. |

---

## 1. Read-only proof

The script makes exactly four plexapi calls, every one a GET:

```
LibrarySection.all               GET /library/sections/{k}/all
LibrarySection.fetchItems        GET, one built query per call
LibrarySection.listFilterChoices GET /library/sections/{k}/{field}
PlexServer.fetchItem             GET /library/metadata/{k}
```

**The spec's literal grep does not reproduce as "empty", and saying so
matters more than the tidier claim.** Run verbatim against the script in §8 it
returns **2 hits** — and both are the module docstring's own *negation* of the
very names the pattern hunts for:

```
$ python -c "
import re
pat = re.compile(r'edit\(|addLabel|removeLabel|upload|delete|refresh')
for i, line in enumerate(open('p9b_probe.py'), 1):
    if pat.search(line): print(i, line.rstrip())
"
10 Nothing else. No ``edit(``, no ``addLabel``, no ``removeLabel``, no ``upload``,
11 no ``delete``, no ``refresh`` -- Step 2's grep over this file proves it.
```

This is the same self-matching false positive as the `X-Plex-Token` hit noted
in §1's scrub check: prose *about* a pattern is matched by that pattern. The
property being asserted is about **executable code**, so the check has to be
scoped to executable code. Stripping comments and string literals with
`tokenize` and re-running the same pattern:

```
$ python -c "
import io, tokenize, re
src = open('p9b_probe.py').read()
lines = {}
for tok in tokenize.generate_tokens(io.StringIO(src).readline):
    if tok.type in (tokenize.COMMENT, tokenize.STRING):
        continue
    lines.setdefault(tok.start[0], []).append(tok.string)
pat = re.compile(r'edit\(|addLabel|removeLabel|upload|delete|refresh|\.put\(|\.post\(|POST|PUT|DELETE')
hits = [(n, ' '.join(v)) for n, v in sorted(lines.items()) if pat.search(' '.join(v))]
print(f'{len(hits)} match(es) in the executable body')
for n, v in hits: print(n, v)
"
0 match(es) in the executable body
```

**0 matches, and this one reproduces as written.** The complementary positive
check — the same tokenised body, asked which plexapi methods it *does* call —
finds **11 call sites across exactly the four GET-only methods above**
(`fetchItems` ×6, `all` ×2, `listFilterChoices` ×2, `fetchItem` ×1) and nothing
else.

Two precautions beyond the spec:

- Listing attributes are read through `object.__getattribute__` — the same trick
  `filter_values._listing_value` uses — so touching `item.genres` in Probe 3
  cannot trigger plexapi's per-item **reload**. That would be extra GETs against
  the operator's server, and it would also silently un-truncate the very genre
  list Probe 3 exists to measure.
- Queries are issued strictly sequentially, one `fetchItems` at a time.

**Scrub verification** (spec Step 4) against the captured output — the host
with and without scheme, the token, and any live `X-Plex-Token` parameter:

```
host_hits=0   token_hits=0   livetoken_hits=0
```

The scrubber also strips the bare **netloc**, not just the full URL, because
plexapi's `BadRequest` messages quote the host without its scheme.

---

## 2. Part A — the dual-path round-trip (movie library, n=1960)

For each attribute: the server path is `build_search_url` + the real
`_Resolver`, fetched; the client path is `parse_filters` + `evaluate` +
`PlexItemView` over one `section.all()` walk. Both collect rating keys.

| Attribute | type | \|srv\| | \|cli\| | \|s−c\| | \|c−s\| | Verdict |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `year` | int | 1299 | 1299 | 0 | 0 | AGREE |
| `resolution` | tag | 1890 | 1890 | 0 | 0 | AGREE |
| `audience_rating` | float | 971 | 971 | 0 | 0 | AGREE |
| `critic_rating` | float | 161 | 161 | 0 | 0 | AGREE |
| `content_rating` | tag | 0 | 0 | 0 | 0 | AGREE *(vacuous — see §2.1)* |
| `added` | date | 651 | 651 | 0 | 0 | AGREE |
| `release` | date | 385 | 385 | 0 | 0 | AGREE |
| `duration` | duration | 513 | 513 | 0 | 0 | AGREE |
| `studio` | str | 43 | 43 | 0 | 0 | AGREE |

**Not one differing rating key across all nine.** The queries sent:

```
year             ?type=1&sort=titleSort&year%3E=2010
resolution       ?type=1&sort=titleSort&resolution=1080
audience_rating  ?type=1&sort=titleSort&audienceRating%3E=7.0
critic_rating    ?type=1&sort=titleSort&rating%3C%3C=5.0
content_rating   ?type=1&sort=titleSort&contentRating=PG-13
added            ?type=1&sort=titleSort&addedAt%3E%3E=2025-07-25
release          ?type=1&sort=titleSort&originallyAvailableAt%3C%3C=2000-01-01
duration         ?type=1&sort=titleSort&duration%3E%3E=7200000.0
studio           ?type=1&sort=titleSort&studio=Columbia%20Pictures
```

`duration` **is** round-trippable, settling the spec's doubt: the bare form is
refused, but the `.gt` spelling renders and agrees.

`studio` is a `str` row, so the bare form is `contains` on both paths. It agreed
exactly at 43 items, so the case-folding hypothesis the spec anticipated **did
not arise**.

### 2.1 Two predicates the spec's literal values could not decide

The spec requires each predicate to be "decidable and none trivially
everything or nothing". Two of its suggested values failed that on this library,
and an agreement at 0 or at the whole library exercises no translation. Both
were re-run against library-derived values; **the originals are reported above
rather than replaced.**

**`content_rating: PG-13` → 0/0, vacuous.** This library does not use MPAA
spellings. Its 22 content ratings are numeric age values — top five `('17',
291), ('16', 236), ('13', 235), ('15', 226), ('14', 164)`. `PG-13` *is* in
`listFilterChoices` (it resolved without raising) but no item carries it, so
both paths correctly returned nothing. Re-run with the library's most common
value:

```
{'content_rating': '17'}   ?type=1&sort=titleSort&contentRating=17
server: 291   client: 291   |s-c|=0 |c-s|=0   AGREE
```

So `content_rating` **is** verified — by the follow-up, not by the spec's value.

**`added` — "a third of the way through the library's range".** Read as the
33rd-percentile *item* this landed on `2025-01-08`, the **minimum** date: about
a third of this library was added on its first day (a bulk import), so the
predicate matched all 1960 and agreed at the trivially-everything end. Read as a
third of the **date range** — the spec's actual words — it is `2025-07-25`,
which splits 651/1960. That is the value in the table above.

```
addedAt range: 2025-01-08 .. 2026-08-25 (n=1960)
  a third through the RANGE = 2025-07-25   <- used
  a third through the ITEMS = 2025-01-08   (rejected: same as the minimum)
```

### 2.2 Row 154 (the clock divergence) — measured, and it had no blast radius

D6 says document, do not reconcile. The probe measured the **exposure**:

```
added boundary sensitivity, around 2025-07-25
  items whose addedAt is within 24h of the boundary: 0
```

Only items within the server/runner offset of the boundary could ever disagree,
and this boundary has **none**. So the 651/651 agreement is *not* evidence that
the two clocks agree — it is evidence that this predicate could not tell.
**Row 154 stands unreconciled and unmeasured**; it should be restated as
still-open rather than citing this run as a pass.

---

## 3. Probe 1 — `show.network` as a SEARCH field: **IT ANSWERS**

The highest-value question in the phase, and the answer flips the expected one.

```
STEP 1: listFilterChoices(field='network', libtype='show') -> 91 choice(s)
  choice: title='5'                 key='126689'
  choice: title='5Star'             key='139106'
  choice: title='A&E'               key='202257'
  choice: title='A&E Crime Central' key='202259'
  choice: title='ABC'               key='12460'

STEP 2: {'all': {'network': '5'}}
  query: /library/sections/2/all?type=2&sort=titleSort&show.network=126689
  -> 2 show(s)

STEP 3: the same shows' listing `studio` values
  'Fireman Sam'                      studio='S4C'
  'Thomas the Tank Engine & Friends' studio='ITV1'
```

**Verdict: `production_network` UNBLOCKS — on a one-network demonstration.**

**Scope of what was actually proven, first.** Step 1 is the decisive part and it
is broad: 91 networks come back. Steps 2-3 are narrow — **one** of those 91
networks, returning **two** shows, verified end to end. So the *mechanism* is
proven and the *breadth* is not: nothing here says all 91 keys resolve, that
none is a stale or empty tag, or that larger networks behave the same. Before
the preset is promised across the vocabulary, sample more of it.

9a proved the `network` *item attribute* is absent on Plex 1.43.4 (0/284 shows
in the listing, absent from `/library/metadata`). That remains true — and it is
a different mechanism from the *search field*, which this run shows is populated
with 91 networks.

Step 3 is the confirmation, not a contradiction: the two Channel 5 shows report
studios `S4C` and `ITV1` — production companies, not the broadcast network. If
`network` were merely an alias for `studio` the values would coincide. They do
not, so `network` carries information no other shipped attribute does.

**The distinction to keep:** `network` is usable in a `plex_search`
and remains **stranded in `filters:`**, because the client side still has no
item attribute to read. This is the same split as `genre` (§5), and it is the
concrete argument for keeping the two vocabularies distinct.

---

## 4. Probe 2 — language search, and the AND-under-`all` question

### The value vocabulary is a MIX (decides the Step 0 option)

`audioLanguage`, 46 values — 2-letter (`es`), locale tags (`es-419`, `en-GB`),
3-letter (`arc`, `mul`, `myn`, `zxx`), and one full English word, `english`.

`subtitleLanguage`, 115 values — the same mix plus `eng`, `fin`, `fil`,
`sr-Latn`, `zh-Hans`, `yue-Hant`.

There is no single form to normalise to. Any option that assumes one shape is
wrong on this library.

### The AND-under-`all` question: confirmed, and it is Kometa's behaviour

| | `all:` | `any:` | one exact variant |
| --- | ---: | ---: | ---: |
| `audio_language: es` (4 variants) | **0** | **24** | 0 |
| `subtitle_language: es` (5 variants) | **0** | **442** | 0 |

```
{'all': {'subtitle_language': 'es'}}
  ...&subtitleLanguage=es&and=1&subtitleLanguage=es-419&and=1&subtitleLanguage=es-ES&and=1&subtitleLanguage=es-CA&and=1&subtitleLanguage=es-LA
  -> 0 item(s)

{'any': {'subtitle_language': 'es'}}
  ...&push=1&subtitleLanguage=es&or=1&subtitleLanguage=es-419&or=1&subtitleLanguage=es-ES&or=1&subtitleLanguage=es-CA&or=1&subtitleLanguage=es-LA&pop=1
  -> 442 item(s)
```

**Verdict: the expansion is effectively unusable under an `all:` base.** An item
would have to carry every Spanish variant at once, and none does. The `any:`
column proves the zero is not "this library has no Spanish content" — there are
442 such films.

The "one exact variant" yardstick (beyond the spec) is what makes this
airtight: bare `es` alone also returns 0, so the library files its Spanish under
the locale tags. Without that column, the `all:` zero could have been misread as
a bug in the expansion rather than in the conjunction.

**This is a row to file, not a divergence to fix** — the builder is faithfully
reproducing `builder.py:4245-4248`. Recommendation: the params
docstring should tell operators to write language predicates under `any:`.

---

## 5. Probe 3 — genre search is NOT subject to the listing's truncation

**RESULT: 5/5.** Five movies re-derived as having 2 genres in the listing and
3+ in `/library/metadata`; each one's **third** genre — the one the listing
drops — searched, and the movie found every time.

| Movie | Listing (truncated) | Metadata | 3rd genre searched | Hits | Present |
| --- | --- | --- | --- | ---: | --- |
| 2 Fast 2 Furious | Action, Crime | + Thriller | `Thriller` | 492 | ✅ |
| The 5th Wave | Action, Adventure | + Science Fiction | `Science Fiction` | 393 | ✅ |
| 10 Lives | Family, Fantasy | + Animation, Comedy | `Animation` | 393 | ✅ |
| 12 Strong | Drama, History | + War, Action | `War` | 73 | ✅ |
| The 13th Warrior | Action, Adventure | + History | `History` | 75 | ✅ |

**Verdict:** the search path does not read the listing and is not subject to its
2-tag cap. `genre` is usable in a `plex_search` while staying deferred in
`filters:` — which is the concrete reason the two vocabularies are worth keeping
distinct, exactly as the spec predicted.

---

## 6. Part C — the six removed spellings: Plex answers all six

Corrections (A) and (B) turned six spellings into refusals because Kometa's
`searches` list has no such key. **This asks only what Plex does — it is
evidence for the narrowing, not a change to it.**

| Written spelling | Query term | Result | Shape |
| --- | --- | ---: | --- |
| `critic_rating:` | `rating=8` | 21 | a proper subset (answered) |
| `critic_rating.not:` | `rating!=8` | 1938 | a proper subset (answered) |
| `audience_rating:` | `audienceRating=8` | 35 | a proper subset (answered) |
| `audience_rating.not:` | `audienceRating!=8` | 1925 | a proper subset (answered) |
| `plays:` | `viewCount=3` | 2 | a proper subset (answered) |
| `plays.not:` | `viewCount!=3` | 105 | a proper subset (answered) |
| *control* — `critic_rating.gte` (ships) | `rating%3E=8` | 158 | answered |
| *control* — `plays.gt` (ships) | `viewCount%3E%3E=3` | 0 | matched nothing |
| *control* — nonsense field | `notAFieldAtAll=8` | **1960** | **ALL (ignored)** |

**The nonsense control is what makes this readable.** Plex silently ignores a
parameter it does not understand and returns the entire library — so "a proper
subset" genuinely means the field was understood and the predicate applied, and
a zero would have meant "understood, matched nothing" rather than "unknown
field".

**Verdict: all six are answered happily by Plex 1.43.4.** The refusals are a
**Kometa restriction this service inherits by choice**, not a Plex limitation.
That is the row the review's second concern asked for. The choice is defensible — one
grammar across both systems beats a spelling that works here and errors in
Kometa — but it should be documented as a choice rather than as a constraint.

One nuance worth recording: `viewCount=3` (2) and `viewCount!=3` (105) sum to
107, not 1960. Plex's `viewCount` predicates only consider items that *have* a
view count, so `plays.not:` does not mean "everything except" — it means
"everything played, except". Relevant to any future `plays` row.

---

## 7. What this changes, and what it does not

**Unblocks**
- `production_network` — `show.network` is a live search field with 91 values.
  Verified end to end on one network's two shows, so wire it with a wider
  sample rather than on this run alone (§3).

**Files as rows (behaviour, not bugs)**
- The language expansion is unusable under `all:`; recommend `any:` in the docs.
- Language values are a mixed vocabulary; no single normalisation is correct.
- The six removed spellings work on Plex — the narrowing is an inheritance.
- `viewCount` predicates are scoped to played items only.

**Stays as it is**
- `network` stays stranded in `filters:` (no item attribute).
- `genre` stays deferred in `filters:`, usable in `plex_search`.
- **Row 154 stays open** — this run measured zero exposure, not agreement.

**No code changed.** The probe found no translation bug; nine of nine
round-trips agree.

---

## 8. The script, verbatim

Run inside the test container with `PROBE_PLEX_URL` / `PROBE_PLEX_TOKEN` in the
environment (passed through as bare `-e NAME`, so neither ever appears on a
command line):

```bash
docker compose -p p9bt5 -f docker-compose.yml -f scratch/isolated-db.yml \
    run --rm --no-deps -e PROBE_PLEX_URL -e PROBE_PLEX_TOKEN \
    test python p9b_probe.py > probe-roundtrip.txt
```

```python
"""Phase 9b -- the read-only live probe.

READ-ONLY. The ONLY plexapi calls this script makes are:

    LibrarySection.all            (GET /library/sections/{k}/all)
    LibrarySection.fetchItems     (GET, one built query per call)
    LibrarySection.listFilterChoices (GET /library/sections/{k}/{field})
    PlexServer.fetchItem          (GET /library/metadata/{k})

Nothing else. No ``edit(``, no ``addLabel``, no ``removeLabel``, no ``upload``,
no ``delete``, no ``refresh`` -- Step 2's grep over this file proves it.

Every line printed goes through ``scrub`` first, so neither the server address
nor the token can reach a recorded file. The script contains no literal URL and
no literal token; both come from the environment for the duration of the run.
"""
import datetime as dt
import os
import re
import sys
from collections import Counter
from types import SimpleNamespace
from urllib.parse import urlsplit

from plexapi.server import PlexServer

from autoposter.collections.builders.plex_search import _Resolver, _base_language_code
from autoposter.collections.filter_values import PlexItemView
from autoposter.collections.filters import BY_NAME, evaluate, parse_filters
from autoposter.collections.search_url import build_search_url


def scrub(text: str) -> str:
    """Nothing leaves this script carrying the server or the token.

    Both the URL and the token appear in plexapi's own exception messages and
    in the ``_server._baseurl`` of every object, so scrubbing at the point of
    PRINTING rather than at the point of construction is the only placement
    that catches all of them. The bare netloc is scrubbed as well as the whole
    URL: plexapi's ``BadRequest`` messages quote the host without the scheme.
    """
    out = str(text)
    url = os.environ["PROBE_PLEX_URL"]
    secrets = [url, url.rstrip("/"), urlsplit(url).netloc, os.environ["PROBE_PLEX_TOKEN"]]
    for secret in secrets:
        if secret:
            out = out.replace(
                secret,
                "<redacted>" if secret == os.environ["PROBE_PLEX_TOKEN"] else "<plex-host>",
            )
    return re.sub(r"X-Plex-Token=[^&\s\"']+", "X-Plex-Token=<redacted>", out)


def emit(*parts: object) -> None:
    print(scrub(" ".join(str(part) for part in parts)), flush=True)


def rule(title: str) -> None:
    emit("")
    emit("=" * 72)
    emit(title)
    emit("=" * 72)


def listing_attr(item: object, name: str):
    """One listing attribute with no chance of a per-item reload -- the same
    ``object.__getattribute__`` trick ``filter_values._listing_value`` uses."""
    try:
        return object.__getattribute__(item, name)
    except AttributeError:
        return None


# --- Part A: the nine dual-resident attributes -------------------------------
#
# (table name, search key, filter key, value) -- the value is filled in at run
# time for the two rows whose predicate has to come from the library itself.
CASES: list[tuple[str, str, str, object]] = [
    ("year", "year.gte", "year.gte", 2010),
    ("resolution", "resolution", "resolution", "1080"),
    ("audience_rating", "audience_rating.gte", "audience_rating.gte", 7),
    ("critic_rating", "critic_rating.lt", "critic_rating.lt", 5),
    ("content_rating", "content_rating", "content_rating", "PG-13"),
    ("added", "added.after", "added.after", None),        # filled from listing
    ("release", "release.before", "release.before", "2000-01-01"),
    ("duration", "duration.gt", "duration.gt", 120),
    ("studio", "studio", "studio", None),                 # filled from listing
]


def round_trip(section, items, resolver, name, skey, fkey, value):
    """One attribute, both paths. Returns the two key sets and the verdict."""
    group_s = parse_filters(
        {skey: value}, field="params.all", searching=True, base="all"
    )
    query = build_search_url(group_s, libtype="movie", resolve_tag=resolver)
    found = section.fetchItems(f"/library/sections/{section.key}/all{query}")
    server_keys = {str(i.ratingKey) for i in found}
    group_c = parse_filters({fkey: value})
    client_keys = {
        str(i.ratingKey) for i in items if evaluate(group_c, PlexItemView(i))
    }
    only_server = server_keys - client_keys
    only_client = client_keys - server_keys
    verdict = "AGREE" if not only_server and not only_client else "DIVERGE"
    return query, server_keys, client_keys, only_server, only_client, verdict


def part_a_addendum(section, items, resolver, boundary: str) -> None:
    """The two round-trips the spec's literal predicates could not decide.

    Neither replaces its row above; both are reported alongside it, because a
    round-trip that agrees at zero and one that agrees at everything are both
    agreements that exercised no translation.
    """
    rule("PART A ADDENDUM -- the two predicates the spec's values could not decide")

    ratings = Counter(
        str(listing_attr(i, "contentRating"))
        for i in items
        if listing_attr(i, "contentRating")
    )
    emit(f"contentRating in the listing: {len(ratings)} distinct; "
         f"top five {ratings.most_common(5)}")
    if ratings:
        chosen = ratings.most_common(1)[0][0]
        query, server_keys, client_keys, only_s, only_c, verdict = round_trip(
            section, items, resolver, "content_rating",
            "content_rating", "content_rating", chosen,
        )
        emit(f"  written  : {{'content_rating': {chosen!r}}}  "
             f"(the library's most common, replacing the spec's 'PG-13')")
        emit(f"  query    : /library/sections/{section.key}/all{query}")
        emit(f"  server   : {len(server_keys)}   client: {len(client_keys)}   "
             f"|s-c|={len(only_s)} |c-s|={len(only_c)}   verdict: {verdict}")

    emit("")
    emit(f"added boundary sensitivity, around {boundary}")
    edge = dt.datetime.fromisoformat(boundary)
    near = sorted(
        (listing_attr(i, "addedAt"), str(listing_attr(i, "title")))
        for i in items
        if listing_attr(i, "addedAt")
        and abs((listing_attr(i, "addedAt") - edge).total_seconds()) <= 86400
    )
    emit(f"  items whose addedAt is within 24h of the boundary: {len(near)}")
    for when, title in near[:10]:
        emit(f"    {when}  {title!r}")
    emit("  (row 154: the server evaluates this boundary in ITS zone and the "
         "client in the runner's. Only these items could disagree; every one "
         "of them landing on the same side is the offset's blast radius "
         "measured, not the offset itself.)")


def part_a(section, items, resolver) -> None:
    rule("PART A -- the dual-path round-trip, movie library")

    added = sorted(
        value for value in (listing_attr(i, "addedAt") for i in items) if value
    )
    # "A third of the way through the library's RANGE" -- the date range, not
    # the item distribution. The first run of this probe took the item at the
    # 33rd percentile instead and landed on the MINIMUM date, because a third
    # of this library was added on its first day: the predicate then matched
    # all 1960 items and the round-trip agreed at the trivially-everything end,
    # which is exactly what the spec says a predicate must not do.
    low, high = added[0].date(), added[-1].date()
    a_third_in = (low + (high - low) / 3).isoformat()
    by_percentile = added[len(added) // 3].date().isoformat()
    emit(f"addedAt range: {low} .. {high} (n={len(added)})")
    emit(f"  a third through the RANGE  = {a_third_in}  <- used")
    emit(f"  a third through the ITEMS  = {by_percentile}  "
         f"(rejected: {'same as the minimum' if by_percentile == low.isoformat() else 'ok'})")

    studios = Counter(
        str(listing_attr(i, "studio"))
        for i in items
        if listing_attr(i, "studio")
    )
    # Not the most common one: a predicate that matches half the library is
    # not a decidable round-trip. The 5th most common is populous enough to
    # be non-trivial and narrow enough to be a real test.
    ranked = studios.most_common()
    studio = ranked[4][0] if len(ranked) > 4 else ranked[0][0]
    emit(f"studios: {len(studios)} distinct; top five "
         f"{[(name, count) for name, count in ranked[:5]]}; chosen = {studio!r}")

    by_key = {str(i.ratingKey): i for i in items}
    filled = [
        (name, skey, fkey,
         a_third_in if name == "added" else studio if name == "studio" else value)
        for name, skey, fkey, value in CASES
    ]

    emit("")
    emit(f"{'attribute':<17} {'type':<9} {'|srv|':>6} {'|cli|':>6} "
         f"{'|s-c|':>6} {'|c-s|':>6}  verdict")
    emit("-" * 72)
    rows = []
    for name, skey, fkey, value in filled:
        row = BY_NAME[name]
        (query, server_keys, client_keys,
         only_server, only_client, verdict) = round_trip(
            section, items, resolver, name, skey, fkey, value
        )
        # An agreement at 0 or at the whole library exercised no translation
        # and must not read as a pass.
        if not server_keys and not client_keys:
            verdict += " (VACUOUS: both empty)"
        elif len(server_keys) == len(items) == len(client_keys):
            verdict += " (VACUOUS: both the whole library)"
        rows.append((name, skey, value, query, server_keys, client_keys,
                     only_server, only_client, verdict))
        emit(f"{name:<17} {row.type:<9} {len(server_keys):>6} "
             f"{len(client_keys):>6} {len(only_server):>6} "
             f"{len(only_client):>6}  {verdict}")

    for (name, skey, value, query, server_keys, client_keys,
         only_server, only_client, verdict) in rows:
        emit("")
        emit(f"--- {name} ---")
        emit(f"  written  : {{{skey!r}: {value!r}}}")
        emit(f"  query    : /library/sections/{section.key}/all{query}")
        emit(f"  server   : {len(server_keys)}   client: {len(client_keys)}"
             f"   verdict: {verdict}")
        for label, keys in (("server-only", only_server), ("client-only", only_client)):
            for key in sorted(keys)[:5]:
                item = by_key.get(key)
                title = listing_attr(item, "title") if item is not None else "?"
                try:
                    seen = PlexItemView(item).get(name) if item is not None else "?"
                except Exception as error:  # noqa: BLE001 -- report, do not stop
                    seen = f"<{type(error).__name__}>"
                extra = ""
                if name == "added" and item is not None:
                    extra = f"  addedAt={listing_attr(item, 'addedAt')}"
                emit(f"  {label}: key={key} {title!r} {name}={seen!r}{extra}")
            if len(keys) > 5:
                emit(f"  {label}: ... and {len(keys) - 5} more")

    part_a_addendum(section, items, resolver, a_third_in)


# --- Part B ------------------------------------------------------------------

def probe_1_network(shows) -> None:
    rule("PART B / PROBE 1 -- show.network as a SEARCH field")
    emit("row: network -> search_field="
         f"{BY_NAME['network'].search_field!r} show_search_field="
         f"{BY_NAME['network'].show_search_field!r} "
         f"search_kinds={BY_NAME['network'].search_kinds}")
    try:
        choices = list(shows.listFilterChoices(field="network", libtype="show"))
    except Exception as error:  # noqa: BLE001
        emit(f"STEP 1: listFilterChoices(field='network', libtype='show') RAISED "
             f"{type(error).__name__}: {error}")
        emit("VERDICT: the search field is not answered either -- "
             "production_network stays GATED.")
        return
    emit(f"STEP 1: listFilterChoices(field='network', libtype='show') -> "
         f"{len(choices)} choice(s)")
    for choice in choices[:5]:
        emit(f"  choice: title={choice.title!r} key={choice.key!r}")
    if not choices:
        emit("VERDICT: empty -- the search field is stranded too. "
             "production_network stays GATED.")
        return

    resolver = _Resolver(
        SimpleNamespace(library="TV Shows", run_cache={}), shows, "show"
    )
    title = str(choices[0].title)
    group = parse_filters(
        {"network": title}, field="params.all", searching=True, base="all"
    )
    query = build_search_url(group, libtype="show", resolve_tag=resolver)
    emit(f"STEP 2: {{'all': {{'network': {title!r}}}}}")
    emit(f"  query: /library/sections/{shows.key}/all{query}")
    found = shows.fetchItems(f"/library/sections/{shows.key}/all{query}")
    emit(f"  -> {len(found)} show(s)")
    emit("STEP 3: the same shows' listing `studio` values")
    for item in found[:10]:
        emit(f"  {listing_attr(item, 'title')!r} studio="
             f"{listing_attr(item, 'studio')!r}")


def probe_2_language(movies) -> None:
    rule("PART B / PROBE 2 -- language search, and AND-under-`all`")
    resolver = _Resolver(
        SimpleNamespace(library="Movies", run_cache={}), movies, "movie"
    )
    for attribute, field in (
        ("audio_language", "audioLanguage"),
        ("subtitle_language", "subtitleLanguage"),
    ):
        emit("")
        emit(f"### {attribute} (Plex field {field!r})")
        try:
            choices = list(movies.listFilterChoices(field=field, libtype="movie"))
        except Exception as error:  # noqa: BLE001
            emit(f"  listFilterChoices RAISED {type(error).__name__}: {error}")
            continue
        keys = sorted(str(choice.key) for choice in choices)
        emit(f"  {len(keys)} value(s): {keys}")

        # A base code with several variants in this library is what makes the
        # AND-under-`all` question decidable at all.
        by_base: dict[str, list[str]] = {}
        for key in keys:
            by_base.setdefault(_base_language_code(key.lower()), []).append(key)
        multi = sorted(
            ((base, variants) for base, variants in by_base.items() if len(variants) > 1),
            key=lambda pair: -len(pair[1]),
        )
        emit(f"  base codes with >1 variant: "
             f"{[(base, variants) for base, variants in multi]}")
        if not multi:
            emit("  no multi-variant code in this library -- the expansion "
                 "cannot be exercised here.")
            continue
        code = multi[0][0]
        for base in ("all", "any"):
            group = parse_filters(
                {attribute: code}, field=f"params.{base}", searching=True, base=base
            )
            query = build_search_url(group, libtype="movie", resolve_tag=resolver)
            found = movies.fetchItems(f"/library/sections/{movies.key}/all{query}")
            emit(f"  {{{base!r}: {{{attribute!r}: {code!r}}}}}")
            emit(f"    query: /library/sections/{movies.key}/all{query}")
            emit(f"    -> {len(found)} item(s)")
        # And the single exact variant, as the yardstick the AND is measured
        # against: one term, no expansion.
        exact = multi[0][1][0]
        group = parse_filters(
            {attribute: exact}, field="params.all", searching=True, base="all"
        )
        query = build_search_url(group, libtype="movie", resolve_tag=resolver)
        found = movies.fetchItems(f"/library/sections/{movies.key}/all{query}")
        emit(f"  yardstick, one exact variant {exact!r}: {len(found)} item(s)")


def probe_3_genre(server, movies, items) -> None:
    rule("PART B / PROBE 3 -- genre search vs the listing's 2-tag truncation")
    resolver = _Resolver(
        SimpleNamespace(library="Movies", run_cache={}), movies, "movie"
    )
    picked = []
    for item in items:
        listed = listing_attr(item, "genres") or []
        if len(listed) != 2:
            continue
        full = server.fetchItem(f"/library/metadata/{item.ratingKey}")
        full_genres = [str(tag.tag) for tag in (full.genres or [])]
        if len(full_genres) < 3:
            continue
        picked.append((
            str(item.ratingKey),
            str(listing_attr(item, "title")),
            [str(tag.tag) for tag in listed],
            full_genres,
        ))
        if len(picked) == 5:
            break

    emit(f"five movies with 2 genres in the listing and 3+ in metadata: "
         f"{len(picked)} found")
    hits = 0
    for key, title, listed, full in picked:
        third = full[2]
        emit("")
        emit(f"  {title!r} (key={key})")
        emit(f"    listing  : {listed}")
        emit(f"    metadata : {full}")
        emit(f"    3rd genre (dropped by the listing): {third!r}")
        group = parse_filters(
            {"genre": third}, field="params.all", searching=True, base="all"
        )
        query = build_search_url(group, libtype="movie", resolve_tag=resolver)
        found = movies.fetchItems(f"/library/sections/{movies.key}/all{query}")
        keys = {str(i.ratingKey) for i in found}
        present = key in keys
        hits += present
        emit(f"    search {{'all': {{'genre': {third!r}}}}} -> {len(found)} item(s); "
             f"this movie present: {present}")
    emit("")
    emit(f"RESULT: {hits}/{len(picked)}")


def part_c_spellings(movies, library_total: int) -> None:
    rule("PART C -- the six removed spellings, as bare server queries")
    emit("These six are refusals in this build because Kometa's `searches` list "
         "has no such key (corrections A and B). The question here is only "
         "what PLEX does with them -- evidence for the narrowing, not a change.")
    emit(f"library total (movie listing walk): {library_total}")

    fields = {
        "critic_rating": BY_NAME["critic_rating"].search_field,
        "audience_rating": BY_NAME["audience_rating"].search_field,
        "plays": BY_NAME["plays"].search_field,
    }
    emit(f"search_field per row: {fields}")

    probes = [
        ("critic_rating:      (bare eq)", f"{fields['critic_rating']}=8"),
        ("critic_rating.not:  (bare ne)", f"{fields['critic_rating']}!=8"),
        ("audience_rating:    (bare eq)", f"{fields['audience_rating']}=8"),
        ("audience_rating.not:(bare ne)", f"{fields['audience_rating']}!=8"),
        ("plays:              (bare eq)", f"{fields['plays']}=3"),
        ("plays.not:          (bare ne)", f"{fields['plays']}!=3"),
        # Two controls: a spelling this build DOES ship, so a zero above can be
        # told apart from a wrong field name.
        ("CONTROL critic_rating.gte (ships)", f"{fields['critic_rating']}%3E=8"),
        ("CONTROL plays.gt (ships)", f"{fields['plays']}%3E%3E=3"),
        # And a deliberately nonsense field, so "Plex ignores what it does not
        # understand" can be told apart from "Plex answered the predicate".
        ("CONTROL nonsense field", "notAFieldAtAll=8"),
    ]
    for label, term in probes:
        query = f"?type=1&sort=titleSort&{term}"
        try:
            found = movies.fetchItems(f"/library/sections/{movies.key}/all{query}")
        except Exception as error:  # noqa: BLE001
            emit(f"  {label:<34} {term:<28} RAISED "
                 f"{type(error).__name__}: {error}")
            continue
        shape = (
            "ALL (predicate ignored)" if len(found) == library_total
            else "0 (matched nothing)" if not found
            else "a proper subset (answered)"
        )
        emit(f"  {label:<34} {term:<28} -> {len(found):>5}  {shape}")


def main() -> int:
    for name in ("PROBE_PLEX_URL", "PROBE_PLEX_TOKEN"):
        if not os.environ.get(name):
            print(f"{name} is not set", file=sys.stderr)
            return 2
    server = PlexServer(os.environ["PROBE_PLEX_URL"], os.environ["PROBE_PLEX_TOKEN"])
    emit(f"probe run {dt.datetime.now().isoformat(timespec='seconds')} "
         f"(runner clock)")
    emit(f"SERVER version={server.version} platform={server.platform} "
         f"{server.platformVersion} friendly={server.friendlyName!r}")
    for section in server.library.sections():
        emit(f"SECTION key={section.key} title={section.title!r} "
             f"type={section.type}")

    movies = server.library.section("Movies")
    shows = server.library.section("TV Shows")
    items = movies.all()          # walk 1 of 2
    emit(f"movie listing walk: {len(items)} item(s)")
    show_items = shows.all()      # walk 2 of 2
    emit(f"show listing walk: {len(show_items)} item(s)")

    resolver = _Resolver(
        SimpleNamespace(library="Movies", run_cache={}), movies, "movie"
    )
    part_a(movies, items, resolver)
    probe_1_network(shows)
    probe_2_language(movies)
    probe_3_genre(server, movies, items)
    part_c_spellings(movies, len(items))
    rule("END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
