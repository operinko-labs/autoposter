# Collection sort titles and separator artwork — the live read-only probe

Roadmap row 49, Task 1. Run against the operator's production Plex on
2026-08-29, **read-only throughout** — the script issues `GET`s only, and
nothing in it writes, edits, creates or deletes anything on the server. The
server address and the token are scrubbed from every line here and from the
captured output; the placeholder is `<plex-host>`. The script itself contains
no literal URL and no literal token — both arrive from the environment for the
duration of the run and are gone with it. Every `except` block prints the
exception's **class name only**, because a plexapi failure's own message quotes
the tokenised URL.

**Server:** the same production instance the 9b and 10a-1 probes ran against
(`docs/research/plex-search-probe/README.md`,
`docs/research/plex-dynamic-probe/README.md`). Sections seen: `Movies` (movie,
374 collections), `TV Shows` (show, 51 collections), `DVR` (movie, 0
collections). Photo and music sections are skipped by the script.

The script is reproduced verbatim in §4 — it is the authority for what was
sent. The captured output is in `probe-output.txt` beside this file, and is
quoted here in full.

---

## 0. Headline

| Question | Verdict | What it decides |
| --- | --- | --- |
| (a) What `titleSort` do adopted collections carry today? | **MEASURED** — 49 collections carry a Kometa `!<NNN>_` prefix: `!020_` (IMDb charts), `!110_` (Common Sense ratings), `!130_` (Oscars) | the migration's "our values replace them" is now a counted fact, not an assumption |
| (b) Does the collections tab honour `titleSort`? | **MEASURED — yes** | the scheme is real, not cosmetic; the PR body needs no disclaimer |
| (c) Which `separators/orig/<key>.jpg` stems exist? | **MEASURED** — `chart`, `award`, `content_rating` exist; `content`, `location`, `media`, `people`, `production`, `time` do not | fixes `groups.SEPARATOR_POSTER_KEYS` to exactly three rows |

Nothing was BLOCKED. All three questions are answered by measurement.

---

## 1. Read-only proof

The script calls, and calls nothing else:

- `PlexServer(url, token)` — the connect.
- `server.library.sections()` — the section list.
- `section.collections()` — one listing per movie/show section.
- `collection.title` / `collection.titleSort` — attribute reads off objects
  that listing already returned.
- `httpx.Client.head(...)` against `raw.githubusercontent.com` — no credential
  is sent, and `HEAD` fetches no body.

No `edit`, no `editSortTitle`, no `addLabel`, no `createCollection`, no
`delete`. The one mutating verb that appears anywhere near this feature —
`editSortTitle` — is not in the script at all.

---

## 2. What it exists to decide

**(a) The unrecorded prefix.** Adjudication C5 says ~35 shipped collections
"each take ONE `editSortTitle` PUT on first pass post-deploy, including
REPLACING adopted Kometa prefixes on managed collections". Nothing in this
codebase has ever written a family sort title — the operator's tidy blocks are
adopted-Kometa inheritance — so what those prefixes actually *are* was never on
record. Until this probe the migration claim rested on inference. It now rests
on a count.

**(b) Does the tab honour `titleSort` at all?** The row-49 recon called this
"very likely but not recorded". It is the load-bearing assumption of the entire
phase: if Plex's collections tab ignores `titleSort` and files collections by
`title`, then every section number, every `!<NNN>_` prefix and every floating
separator is decoration that changes nothing an operator can see, and the PR
body would have to say so.

**(c) Which separator artwork exists.** `posters.hosted_poster_url` builds
`separators/orig/content_rating.jpg` for the one shipped divider. Generalising
one divider into ten needs to know which of the ten group keys
`Kometa-Team/Default-Images` actually hosts art for. A guess here 404s and
leaves a divider quietly bare; the measurement makes
`groups.SEPARATOR_POSTER_KEYS` a table of facts, and every group absent from it
takes the graceful no-poster path by construction.

---

## 3. Results

### 3a. Adopted sort titles — MEASURED

Three distinct Kometa prefixes are in use across the two real libraries, on 49
collections in total:

| Prefix | Movies | TV Shows | Total | The family carrying it |
| --- | --- | --- | --- | --- |
| `!020_` | 3 | 2 | 5 | IMDb charts (`IMDb Top 250`, `IMDb Popular`, `IMDb Lowest Rated`) |
| `!110_` | 20 | 17 | 37 | Common Sense age ratings, the `Ratings Collections` divider included |
| `!130_` | 7 | 0 | 7 | Oscars — the two winner collections and five ceremony years |
| **total** | **30** | **19** | **49** | |

Three shapes are worth reading closely, because they are what our derived
values replace:

- `IMDb Top 250` → `'!020_IMDb Top 250'`. Kometa's chart section is `020`.
  **Ours is `010`** — the two collide in the sense that our *awards* number
  (`020`) is Kometa's *charts* number. That is not a correctness problem (our
  value simply overwrites theirs on collections this service manages) but it
  does mean a half-migrated library shows awards and charts interleaved for
  exactly one pass. Noted, not fixed: renumbering to dodge a transient is worse
  than the transient.
- `Oscars Best Picture Winners` → `'!130_Oscars !1'`. Kometa's award
  collections carry a hand-ordered suffix (`!1`, `!2`) rather than the title,
  so the sort title and the title diverge entirely. Our formula
  (`!<section>_<title>`) restores the title, which is a **visible reordering**
  of that block on first pass: the two winner collections stop leading their
  section and file alphabetically among the ceremony years. Worth one sentence
  in the PR body.
- `Ratings Collections` → `'!110_!Ratings Collections'`, and `Age 13+ Movies` →
  `'!110_13_Age 13+ Movies'`. The divider's extra `!` is confirmed live: it is
  the float trick, and it works. The members carry a two-digit *ordering* key
  (`13_`) between the section and the title — a slot our sort-title formula
  leaves empty (`order_<<key>>: ""`), so `Age 2+` files after `Age 18+`
  alphabetically once ours lands. Also a visible reordering, also worth the
  sentence. The Common Sense block is the one place this phase's formula is
  *less* ordered than what it replaces.

`Not Rated Movies` carries `'!110_~Not Rated Movies'` — a `~` sentinel to pin
it last. Ours drops that too, for the same reason.

### 3b. The collections tab honours `titleSort` — MEASURED, yes

The plan's original three-line test was two equality comparisons, and **both
came back `False` on the real libraries**. That is not the answer it looks
like: the comparison is between Plex's ordering and Python's `str.lower()`
ordering, and those are different collations. Plex files `!110_~Not Rated
Movies` *before* `!110_01_Age 1+ Movies`; raw ASCII does the reverse (`!`=33 <
`0`=48 < `~`=126). Two such punctuation disagreements in a 374-collection
listing are enough to make an exact-equality test say `False` about an ordering
that is otherwise entirely `titleSort`'s.

So the script was amended, after that first run, with two measurements no
collation of punctuation can flip. Both are quoted from the output below.

**The block test** — does the tab keep whole `!<NNN>_` blocks together, in
section order, ahead of every collection carrying no prefix?

```
    prefix blocks, in listed order          : 020, 110, 130, zzz
    each block is contiguous                : True
    blocks ascend, unprefixed last          : True
```

(`zzz` is the sentinel for "no prefix". The TV library gives the same answer
with the blocks it has: `020, 110, zzz`.)

**The inversion count** — how many adjacent pairs in Plex's listing are out of
order under each candidate key:

| Library | under `title` | under `titleSort` | of |
| --- | --- | --- | --- |
| Movies | 53 | **2** | 373 |
| TV Shows | 4 | **1** | 50 |

**And the heads of the three orderings**, which settle it on their own:

```
    first 5 as listed: IMDb Lowest Rated, IMDb Popular, IMDb Top 250, Ratings Collections, Not Rated Movies
    first 5 by title : 101 Dalmatians (Animated) Collection, 28 Days/Weeks/Years Later Collection, 300 Collection, A Nightmare on Elm Street Collection, A Quiet Place Collection
    first 5 by sort  : IMDb Lowest Rated, IMDb Popular, IMDb Top 250, Ratings Collections, Age 1+ Movies
```

Plex's own listing leads with the four `!`-prefixed families in section order.
Under `title` it would lead with `101 Dalmatians`. The tab is sorted by
`titleSort`, and the residual two-of-373 disagreement is Plex's collation of
punctuation inside a block, not a different sort key.

**Verdict: MEASURED — the collections tab honours `titleSort`.** The scheme is
not cosmetic. The PR body needs no disclaimer.

One caveat recorded rather than resolved: `section.collections()` returns the
library's default collection ordering, which is the tab's default. An operator
who has switched their tab to "Recently Added" or "By Rating" sees that instead
— for them the scheme is inert until they switch back. That is true of Kometa's
scheme too and is not something this phase can or should change.

### 3c. Separator artwork — MEASURED

`HEAD` against
`https://raw.githubusercontent.com/Kometa-Team/Default-Images/master/separators/orig/<key>.jpg`:

| Candidate stem | Status | Group it would serve |
| --- | --- | --- |
| `chart` | **200** | `charts` |
| `award` | **200** | `awards` |
| `content_rating` | **200** | `content_ratings` (the one already on record) |
| `content` | 404 | `content` — **no poster** |
| `location` | 404 | `location` — **no poster** |
| `media` | 404 | `media` — **no poster** |
| `people` | 404 | `people` — **no poster** |
| `production` | 404 | `production` — **no poster** |
| `time` | 404 | `time` — **no poster** |
| `collectionless`, `genre`, `decade`, `year`, `studio`, `country`, `franchise` | 200 | none — these are Kometa's per-*collection* separator stems, not our group keys; recorded because the probe asked, unused because no group claims them |

So `groups.SEPARATOR_POSTER_KEYS` is exactly three rows:

```python
SEPARATOR_POSTER_KEYS: dict[str, str] = {
    "charts": "chart",
    "awards": "award",
    "content_ratings": "content_rating",
}
```

The `operator` group is absent too, deliberately — there is no upstream art for
"the operator's own collections" and inventing one is not this phase's job.

Six of the ten groups therefore get a separator with **no poster**, which is
graceful by construction rather than by a branch anyone had to write:
`posters.hosted_poster_url` returns `None` for a kind it cannot build a path
for, and `apply_poster` reads `None` as "leave the poster alone" rather than
guessing a URL that would 404 and leave the collection quietly bare.

### 3d. The captured output, verbatim

The 374-line `titleSort` listing for the Movies library is elided here with a
`[…]` marker for readability; it is complete and unelided in
`probe-output.txt`. Everything else is byte-for-byte. The six
container-creation lines compose wrote to stderr ahead of the script's first
line are not part of the script's output and are not reproduced.

```
=== section Movies (movie): 374 collection(s)
--- titleSort of every collection carrying a non-empty one
    IMDb Lowest Rated                            '!020_IMDb Lowest Rated'
    IMDb Popular                                 '!020_IMDb Popular'
    IMDb Top 250                                 '!020_IMDb Top 250'
    Ratings Collections                          '!110_!Ratings Collections'
    Not Rated Movies                             '!110_~Not Rated Movies'
    Age 1+ Movies                                '!110_01_Age 1+ Movies'
    Age 2+ Movies                                '!110_02_Age 2+ Movies'
    […]
    Age 18+ Movies                               '!110_18_Age 18+ Movies'
    Oscars Best Picture Winners                  '!130_Oscars !1'
    Oscars Best Director Winners                 '!130_Oscars !2'
    Oscars Winners 2022                          '!130_Oscars Winners 2022'
    Oscars Winners 2023                          '!130_Oscars Winners 2023'
    Oscars Winners 2024                          '!130_Oscars Winners 2024'
    Oscars Winners 2025                          '!130_Oscars Winners 2025'
    Oscars Winners 2026                          '!130_Oscars Winners 2026'
    28 Days/Weeks/Years Later Collection         '28 Days/Weeks/Years Later Collection'
    […]
    Zootopia Collection                          'Zootopia Collection'
--- tab ordering
    default listing == sorted by title      : False
    default listing == sorted by titleSort  : False
    the two candidate orders differ         : True
    first 5 as listed: IMDb Lowest Rated, IMDb Popular, IMDb Top 250, Ratings Collections, Not Rated Movies
    first 5 by title : 101 Dalmatians (Animated) Collection, 28 Days/Weeks/Years Later Collection, 300 Collection, A Nightmare on Elm Street Collection, A Quiet Place Collection
    first 5 by sort  : IMDb Lowest Rated, IMDb Popular, IMDb Top 250, Ratings Collections, Age 1+ Movies
    prefix blocks, in listed order          : 020, 110, 130, zzz
    each block is contiguous                : True
    blocks ascend, unprefixed last          : True
    adjacent inversions under title         : 53 of 373
    adjacent inversions under titleSort      : 2 of 373
=== section TV Shows (show): 51 collection(s)
--- titleSort of every collection carrying a non-empty one
    IMDb Popular                                 '!020_IMDb Popular'
    IMDb Top 250                                 '!020_IMDb Top 250'
    Ratings Collections                          '!110_!Ratings Collections'
    Not Rated Shows                              '!110_~Not Rated Shows'
    Age 2+ Shows                                 '!110_02_Age 2+ Shows'
    […]
    Age 18+ Shows                                '!110_18_Age 18+ Shows'
    Action Shows                                 'Action Shows'
    […]
    X-Men Universe                               'X-Men Universe'
--- tab ordering
    default listing == sorted by title      : False
    default listing == sorted by titleSort  : False
    the two candidate orders differ         : True
    first 5 as listed: IMDb Popular, IMDb Top 250, Ratings Collections, Not Rated Shows, Age 2+ Shows
    first 5 by title : Action Shows, Adventure Shows, Age 10+ Shows, Age 11+ Shows, Age 12+ Shows
    first 5 by sort  : IMDb Popular, IMDb Top 250, Ratings Collections, Age 2+ Shows, Age 3+ Shows
    prefix blocks, in listed order          : 020, 110, zzz
    each block is contiguous                : True
    blocks ascend, unprefixed last          : True
    adjacent inversions under title         : 4 of 50
    adjacent inversions under titleSort      : 1 of 50
=== section DVR (movie): 0 collection(s)
--- titleSort of every collection carrying a non-empty one
--- tab ordering
    default listing == sorted by title      : True
    default listing == sorted by titleSort  : True
    the two candidate orders differ         : False
    first 5 as listed: 
    first 5 by title : 
    first 5 by sort  : 
    prefix blocks, in listed order          : 
    each block is contiguous                : True
    blocks ascend, unprefixed last          : True
    adjacent inversions under title         : 0 of 0
    adjacent inversions under titleSort      : 0 of 0
=== Default-Images separators/orig
    chart              200
    award              200
    content_rating     200
    content            404
    location           404
    media              404
    people             404
    production         404
    time               404
    collectionless     200
    genre              200
    decade             200
    year               200
    studio             200
    country            200
    franchise          200
```

One incidental observation, recorded because it will matter to whoever writes
the collision tests: Plex generates a `titleSort` of its own for collections
nobody has set one on — `Pokémon Collection` carries `'Pokemon Collection'`,
`The Avengers Collection` carries `'Avengers Collection'`. Those are Plex's,
not a prior tool's, and this phase must not read a non-empty `titleSort` as
evidence that something else manages the collection.

---

## 4. The script, verbatim

```python
"""Row 49 Task 1: three read-only measurements. Nothing is written to Plex."""
import os
from urllib.parse import urlsplit

import httpx
from plexapi.server import PlexServer

# The candidate separator artwork stems, one per group in the canonical order.
# "content_rating" is the one already on record -- ``posters.hosted_poster_url``
# builds ``separators/orig/content_rating.jpg`` for the shipped divider today.
CANDIDATE_KEYS = [
    "chart", "award", "content_rating", "content", "location",
    "media", "people", "production", "time", "collectionless",
    "genre", "decade", "year", "studio", "country", "franchise",
]
DEFAULT_IMAGES_BASE = (
    "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"
)


def scrub(text: str) -> str:
    out = str(text)
    url = os.environ["PROBE_PLEX_URL"]
    for secret in [url, url.rstrip("/"), urlsplit(url).netloc,
                   os.environ["PROBE_PLEX_TOKEN"]]:
        if secret:
            out = out.replace(secret, "<plex-host>")
    return out


def probe_plex() -> None:
    try:
        server = PlexServer(os.environ["PROBE_PLEX_URL"],
                            os.environ["PROBE_PLEX_TOKEN"])
        sections = list(server.library.sections())
    except Exception as error:  # class name only, never the message
        print("UNREACHABLE (%s) -- Plex probes BLOCKED" % type(error).__name__)
        return

    for section in sections:
        if section.type not in ("movie", "show"):
            continue
        try:
            collections = list(section.collections())
        except Exception as error:
            print(scrub("=== section %s: REFUSED (%s)"
                        % (section.title, type(error).__name__)))
            continue

        print(scrub("=== section %s (%s): %d collection(s)"
                    % (section.title, section.type, len(collections))))

        # (a) what sort titles the collections we would manage carry TODAY.
        print("--- titleSort of every collection carrying a non-empty one")
        for collection in collections:
            sort = getattr(collection, "titleSort", None) or ""
            if sort:
                print(scrub("    %-44s %r" % (collection.title, sort)))

        # (b) does the tab honour titleSort? The listing order Plex returns
        # with no sort argument is the tab's own. Compare it against the two
        # candidate orderings. A "!"-prefixed separator sorts first under
        # titleSort and under R under title, so the two orders differ and the
        # comparison is decisive rather than a coincidence.
        listed = [c.title for c in collections]
        by_title = sorted(listed, key=lambda t: t.lower())
        by_sort = [
            c.title for c in sorted(
                collections,
                key=lambda c: (getattr(c, "titleSort", None) or c.title).lower(),
            )
        ]
        print("--- tab ordering")
        print("    default listing == sorted by title      : %s"
              % (listed == by_title))
        print("    default listing == sorted by titleSort  : %s"
              % (listed == by_sort))
        print("    the two candidate orders differ         : %s"
              % (by_title != by_sort))
        print(scrub("    first 5 as listed: %s" % ", ".join(listed[:5])))
        print(scrub("    first 5 by title : %s" % ", ".join(by_title[:5])))
        print(scrub("    first 5 by sort  : %s" % ", ".join(by_sort[:5])))

        # The two equality lines above are NOT decisive on a real library and
        # this run proved it: both came back False, because Python's
        # ``str.lower()`` ordering is not Plex's collation (Plex files
        # ``!110_~Not Rated`` before ``!110_01_Age 1+``; raw ASCII does the
        # reverse). So measure the property row 49 actually depends on, which
        # no collation of punctuation can flip: does the tab keep whole
        # ``!<NNN>_`` blocks together and in section order, ahead of every
        # collection carrying no prefix at all? Adjacent-pair inversion counts
        # under each candidate key are printed alongside as a second, blunter
        # reading of the same question.
        def prefix_of(collection) -> str:
            sort = getattr(collection, "titleSort", None) or ""
            if len(sort) > 4 and sort[0] == "!" and sort[1:4].isdigit():
                return sort[1:4]
            return "zzz"  # no prefix: belongs after every numbered block

        blocks = [prefix_of(c) for c in collections]
        seen_blocks: list[str] = []
        for block in blocks:
            if not seen_blocks or seen_blocks[-1] != block:
                seen_blocks.append(block)
        print("    prefix blocks, in listed order          : %s"
              % ", ".join(seen_blocks))
        print("    each block is contiguous                : %s"
              % (len(seen_blocks) == len(set(seen_blocks))))
        print("    blocks ascend, unprefixed last          : %s"
              % (seen_blocks == sorted(seen_blocks)))

        def inversions(keys) -> int:
            return sum(1 for a, b in zip(keys, keys[1:]) if a > b)

        print("    adjacent inversions under title         : %d of %d"
              % (inversions([t.lower() for t in listed]), max(len(listed) - 1, 0)))
        print("    adjacent inversions under titleSort      : %d of %d"
              % (inversions([(getattr(c, "titleSort", None) or c.title).lower()
                             for c in collections]), max(len(listed) - 1, 0)))


def probe_images() -> None:
    # (c) which separator artwork stems exist. No credential, no Plex, and the
    # URL is built from a constant -- nothing here needs scrubbing, and nothing
    # here is printed from an exception's message either.
    print("=== Default-Images separators/orig")
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        for key in CANDIDATE_KEYS:
            url = "%s/separators/orig/%s.jpg" % (DEFAULT_IMAGES_BASE, key)
            try:
                response = client.head(url)
            except Exception as error:
                print("    %-18s ERROR (%s)" % (key, type(error).__name__))
                continue
            print("    %-18s %d" % (key, response.status_code))


if __name__ == "__main__":
    probe_plex()
    probe_images()
```

The script lived at the worktree root as `probe_sort.py` for the length of the
run and was deleted before the commit — it is reproduced here in full instead,
which is the shape `docs/research/plex-dynamic-probe/README.md` established.

---

## 5. How it was run

The credentials pass as bare `-e NAME`, so neither value ever appears on a
command line:

```bash
export PROBE_PLEX_URL=...       # read from the operator's config, never echoed
export PROBE_PLEX_TOKEN=...     # read from .env's AUTOPOSTER_PLEX_TOKEN
export MSYS_NO_PATHCONV=1       # Git Bash would otherwise rewrite /app/...

docker compose -p p49t1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
    run --rm --no-deps -e PROBE_PLEX_URL -e PROBE_PLEX_TOKEN \
    test python /app/probe_sort.py > .superpowers/p49-probe.log 2>&1
docker compose -p p49t1 down
```

The scrub was then verified against the captured log by counting occurrences of
each needle — the counts are printed, the needles never are:

```
file=.superpowers/p49-probe.log url_hits=0 host_hits=0 token_hits=0
file=probe_sort.py              url_hits=0 host_hits=0 token_hits=0
```

Zero on all six. The `<plex-host>` placeholder does not appear in the captured
output either, which is the stronger result: the scrub never had to fire,
because no line the script printed ever contained the address or the token in
the first place.
