# Plex dynamic types — the live read-only probe

Phase 10a-1. Run against the operator's production Plex on 2026-08-27,
**read-only throughout**. The server address and the token are scrubbed from
every line here and from the captured output; the placeholder is `<plex-host>`.
The script itself contains no literal URL and no literal token — both arrive
from the environment for the duration of the run and are gone with it.

**Server:** the same production instance 9b's probe ran against
(`docs/research/plex-search-probe/README.md`). Sections seen: 1 `Movies`
(movie), 2 `TV Shows` (show), 5 `DVR` (movie, 2 items). Photo sections are
skipped by the script.

**What it exists to decide.** Phase 10a is scoped to "the types
`listFilterChoices` can enumerate TODAY", and named two rows whose choices
listing was *unproven*: `network` — 9b proved the SEARCH field answers with 91
networks, and the CHOICES listing is the same family, but that is an inference,
not a measurement — and `country` (⚠️ the row shipped as a search
attribute without its enumerability ever being asked). Both are **fail-closed**:
an attribute that refuses, answers zero, or cannot be probed at all does not
ship a dynamic type row.

The script is reproduced verbatim in §4 — it is the authority for what was sent.

---

## 0. Headline

| Attribute (library) | Probe verdict | What the type table does |
| --- | --- | --- |
| **`network`** (TV Shows, show) | **ANSWERS — 91 values** | ships as written, the count in the row's `note` |
| **`country`** (Movies, movie) | **ANSWERS — 63 values** | ships as written, movie-only per upstream |
| the other eight scoped rows | answer on every library type their `search_kinds` claims | ship as written |
| `decade` on a show library | **not asked** | show-decade is OUT of the phase; the probe's table carries no show field for it, which is `FilterAttribute.show_search_field=None` spelled by value |

Neither fail-closed branch fired. Ten rows ship.

---

## 1. Read-only proof

The script touches three plexapi calls, every one a GET:

```
PlexServer.__init__               GET /
library.sections                  GET /library/sections
LibrarySection.listFilterChoices  GET /library/sections/{k}/{field}
```

**The probe script's own docstring named only the third**, and the shipped one
names all three: the connect and the section listing are what reaching
`listFilterChoices` requires, and a read-only claim that omits two of its three
requests is a claim a reader cannot check. Corrected in place rather than left
for §4's reader to notice.

9b's tokenised check, re-run over this script — comments and string literals
stripped, so prose *about* a forbidden pattern cannot match it:

```
$ python -c "
import io, tokenize, re
src = open('p10a_probe.py').read()
lines = {}
for tok in tokenize.generate_tokens(io.StringIO(src).readline):
    if tok.type in (tokenize.COMMENT, tokenize.STRING):
        continue
    lines.setdefault(tok.start[0], []).append(tok.string)
pat = re.compile(r'edit\(|addLabel|removeLabel|upload|delete|refresh|\.put\(|\.post\(|POST|PUT|DELETE')
hits = [(n, ' '.join(v)) for n, v in sorted(lines.items()) if pat.search(' '.join(v))]
print(f'{len(hits)} match(es) in the executable body')
"
0 match(es) in the executable body
```

The complementary positive check — the same tokenised body asked which methods
it *does* call — finds exactly `listFilterChoices` (×1), `sections` (×1) and the
`PlexServer` constructor, alongside four stdlib string calls (`join`, `replace`,
`rpartition`, `rstrip`) and nothing else.

**Scrub verification** over the captured output — the host with and without
scheme, and any live `X-Plex-Token` parameter:

```
host_hits=0   netloc_hits=0   livetoken_hits=0
```

One precaution beyond the spec: the `PlexServer` connect is itself wrapped
class-name-only. An unhandled failure there would put plexapi's own message —
which quotes the tokenised URL — into the traceback, and the traceback is the
captured log. That wrap is what produced the first run's single line
(`UNREACHABLE (Unauthorized) -- probe BLOCKED`, from a token read with its
surrounding quotes still attached) instead of a leak.

---

## 2. The output, verbatim

Exactly as the script printed it, less the two `docker compose`
container-progress lines the capture's `tee` also caught.

```
=== section Movies (movie)
  year                 87 value(s)  2026=2026, 2025=2025, 2024=2024, 2023=2023, 2022=2022
  decade               12 value(s)  2020=2020s, 2010=2010s, 2000=2000s, 1990=1990s, 1980=1980s
  content_rating       39 value(s)  10=10, 11=11, 12=12, 13=13, 14=14
  studio              824 value(s)  1492%2520Pictures=1492 Pictures, 2%2520Player%2520Productions=2 Player Productions, 20th%2520Century%2520Fox=20th Century Fox, 20th%2520Century%2520Fox%2520Animation=20th Century Fox Animation, 20th%2520Century%2520Fox%2520Home%2520Entertainment=20th Century Fox Home Entertainment
  genre                21 value(s)  482=Action, 208985=Action & Adventure, 483=Adventure, 936=Animation, 656=Comedy
  country              63 value(s)  137628=Argentina, 7342=Australia, 157671=Austria, 7465=Belgium, 29407=Bolivarian Republic of Venezuela
  resolution            7 value(s)  4k=4K, 2k=2K, 1080=1080p, 720=720p, 576=576p
  audio_language       46 value(s)  en=English, no=Norwegian, ro=Romanian, nl=Dutch, es-419=Spanish (Latin America)
  subtitle_language   115 value(s)  en=English, es=Spanish, fr=French, de=German, it=Italian
  network            n/a for this library type
=== section TV Shows (show)
  year                 39 value(s)  2026=2026, 2025=2025, 2024=2024, 2023=2023, 2022=2022
  decade             n/a for this library type
  content_rating       17 value(s)  10=10, 11=11, 12=12, 13=13, 14=14
  studio               74 value(s)  A%2526E=A&E, ABC=ABC, Adult%2520Swim=Adult Swim, AMC=AMC, Apple%2520TV=Apple TV
  genre                14 value(s)  208985=Action & Adventure, 936=Animation, 656=Comedy, 1859=Crime, 2256=Documentary
  country              18 value(s)  7342=Australia, 7465=Belgium, 733=Canada, 6131=Finland, 31455=France
  resolution            6 value(s)  4k=4K, 1080=1080p, 720=720p, 480=480p, sd=SD
  audio_language       30 value(s)  en=English, fi=Finnish, en-US=English, de=German, fr=French
  subtitle_language   104 value(s)  en=English, fi=Finnish, en-US=English, da=Danish, de=German
  network              91 value(s)  126689=5, 139106=5Star, 202257=A&E, 202259=A&E Crime Central, 12460=ABC
=== section DVR (movie)
  year                  2 value(s)  2026=2026, 2021=2021
  decade                1 value(s)  2020=2020s
  content_rating        2 value(s)  16=16, None=None
  studio                1 value(s)  WarParty%2520Films=WarParty Films
  genre                 3 value(s)  482=Action, 485=Science Fiction, 553=Thriller
  country               0 value(s)
  resolution            1 value(s)  1080=1080p
  audio_language        1 value(s)  fi=Finnish
  subtitle_language     3 value(s)  hi=Hindi, en=English, fi=Finnish
  network            n/a for this library type
```

---

## 3. What it decides, beyond the two verdicts

**Both fail-closed rows answer, so `DYNAMIC_TYPES` ships all ten scoped rows.**
`network` answers with 91 values on the show library — the same 91 the 9b search
probe found, which is the two halves of the family agreeing rather than one
half being assumed from the other. `country` answers with 63 on the movie
library, which is roadmap row 174's enumerability settled by measurement.

**`key_from` is confirmed per row, and it is not guessable from the name.** The
sample column is the whole reason to read the raw output:

| Row | `choice.key` | `choice.title` | `key_from` | Why |
| --- | --- | --- | --- | --- |
| `decade` | `1980` | `1980s` | `key` | the query needs the number; the title names the collection |
| `resolution` | `4k` | `4K` | `key` | `resolution=4k` is what Plex answers |
| `audio_language` | `en`, `es-419` | `English` | `key` | the language keys are the search vocabulary |
| `genre` | `482` | `Action` | `title` | the key is an opaque server-side tag id |
| `country` | `137628` | `Argentina` | `title` | same shape as `genre` |
| `network` | `126689` | `5` | `title` | same shape as `genre` |
| `content_rating` | `13` | `13` | `title` | key and title are the same string |
| `year` | `2026` | `2026` | `title` | same |
| `studio` | `20th%2520Century%2520Fox` | `20th Century Fox` | `title` | see below |

`studio`'s key comes back **double percent-encoded** — `%2520` is `%20`
percent-encoded again. Keying a studio bucket on `choice.key` would write
`studio.is=1492%2520Pictures` into the query and match nothing; `key_from:
title` writes `1492 Pictures`, which is what `studio` (a `str` attribute, quoted
verbatim rather than resolved through the tag vocabulary) needs. The tag rows
(`genre`, `country`, `network`, `content_rating`) take the title too, and the
resolver maps it back to the opaque key on the way out — which is the round trip
`LibraryTagResolver.__call__` already made for a handwritten `genre: Horror`.

**A third movie-type section exists and is not a library.** `DVR` reports as
`movie`, holds two items, and answers `country` with **0 values**. It is the
concrete instance of the fail-closed case the type table's `note` describes for
`network` ("a library whose agent cannot answer enumerates nothing and the
definition refuses with the count") — a real section where a `country` family
would build a collection named after nothing. Nothing in this task acts on it;
it is recorded because the fan-out cap and the empty-enumeration refusal (Tasks
4/5) now have a real example to be tested against rather than a hypothetical.

**Fan-out, measured** — the number the collection cap is chosen against, per library:

| Type | Movies | TV Shows |
| --- | --- | --- |
| `year` | 87 | 39 |
| `decade` | 12 | — |
| `content_rating` | 39 | 17 |
| `studio` | **824** | 74 |
| `genre` | 21 | 14 |
| `country` | 63 | (18, not shipped — movie-only) |
| `resolution` | 7 | 6 |
| `audio_language` | 46 | 30 |
| `subtitle_language` | **115** | **104** |
| `network` | — | 91 |

`studio` at **824** is the headline: an uncapped `type: studio` on this library
would create eight hundred and twenty-four collections in one pass. The
roadmap's fan-out risk note names `year` (87 here) as its example; the real
worst case is an order of magnitude worse, and `subtitle_language` (115/104) is
the second. The `max_collections` floor should be chosen against 824, not
against 87.

---

## 4. The script, verbatim

```python
"""Phase 10a -- the read-only live probe.

READ-ONLY. The plexapi surface this script touches is three calls, every one a
GET -- the spec's text named only the third, and the other two are the connect
and the section listing that reaching it requires, so they are named here rather
than left for a reader to discover:

    PlexServer.__init__               GET /
    LibraryHub / library.sections     GET /library/sections
    LibrarySection.listFilterChoices  GET /library/sections/{k}/{field}

Nothing else. No ``edit(``, no ``addLabel``, no ``removeLabel``, no ``upload``,
no ``delete``, no ``refresh``.

Every line printed goes through ``scrub`` first, so neither the server address
nor the token can reach a recorded file. The script contains no literal URL and
no literal token; both come from the environment for the duration of the run.
"""
import os
from urllib.parse import urlsplit

from plexapi.server import PlexServer

# (our attribute name, movie field, show field) -- the movie/show columns are
# ``FilterAttribute.field_for``'s, spelled out here so the probe depends on the
# table only by value.
FIELDS = [
    ("year", "year", "show.year"),
    ("decade", "decade", None),
    ("content_rating", "contentRating", "show.contentRating"),
    ("studio", "studio", "show.studio"),
    ("genre", "genre", "show.genre"),
    ("country", "country", "show.country"),
    ("resolution", "resolution", "episode.resolution"),
    ("audio_language", "audioLanguage", "episode.audioLanguage"),
    ("subtitle_language", "subtitleLanguage", "episode.subtitleLanguage"),
    ("network", None, "show.network"),
]


def scrub(text: str) -> str:
    out = str(text)
    url = os.environ["PROBE_PLEX_URL"]
    for secret in [url, url.rstrip("/"), urlsplit(url).netloc,
                   os.environ["PROBE_PLEX_TOKEN"]]:
        if secret:
            out = out.replace(secret, "<plex-host>")
    return out


def main() -> None:
    # The connect is wrapped for the same reason every wrap in this repo is:
    # a plexapi failure's own message quotes the tokenised URL, and an
    # unhandled one here would put it in the traceback -- which is the
    # captured log. Class name only, like ``_raw_choices``'s.
    try:
        server = PlexServer(os.environ["PROBE_PLEX_URL"],
                            os.environ["PROBE_PLEX_TOKEN"])
        sections = list(server.library.sections())
    except Exception as error:  # class name only, never the message
        print("UNREACHABLE (%s) -- probe BLOCKED" % type(error).__name__)
        return
    for section in sections:
        if section.type not in ("movie", "show"):
            continue
        print(scrub("=== section %s (%s)" % (section.title, section.type)))
        for name, movie_field, show_field in FIELDS:
            field = movie_field if section.type == "movie" else show_field
            if field is None:
                print("  %-18s n/a for this library type" % name)
                continue
            scope, _, plain = field.rpartition(".")
            scope = scope or section.type
            try:
                choices = list(section.listFilterChoices(field=plain, libtype=scope))
            except Exception as error:  # class name only, never the message
                print("  %-18s REFUSED (%s)" % (name, type(error).__name__))
                continue
            sample = ", ".join(
                "%s=%s" % (c.key, c.title) for c in choices[:5]
            )
            print(scrub("  %-18s %4d value(s)  %s" % (name, len(choices), sample)))


if __name__ == "__main__":
    main()
```

**How it was run** — the credentials pass as bare `-e NAME`, so neither ever
appears on a command line:

```bash
docker compose -p p10at2 -f docker-compose.yml -f scratch/isolated-db.yml \
    run --rm --no-deps -e PROBE_PLEX_URL -e PROBE_PLEX_TOKEN \
    test python p10a_probe.py 2>&1 | tee scratch/run-probe.log
```

The script is not committed: it exists for the length of the probe and this file
is the artefact that ships, the same arrangement
`docs/research/plex-search-probe/README.md` §8 documents.

---

## 5. Follow-up — phase 10a-2, the two decoder GETs (2026-08-27)

Run against the same production instance, **read-only, two GETs and nothing
else**. Both settle a premise the Common Sense equivalence proof
(`tests/oracle/10a2/`) was resting on without measurement. Same scrub rule as
the rest of this file: the placeholder is `<plex-host>`, and the script printed
counts only — never a URL, never the token, which arrived from `.env` for the
length of the run.

### Probe P5 — does the server fold `+` to a space?

The proof's decoder reads a query the way plexapi *encodes* it
(`urlencode` = `quote_plus`, so a space goes out as `+`). Whether the **server**
folds `+` back to a space was never measured; it decides whether `contentRating=12+`
is searched for as `12+` or as `12 `, and therefore which six of the ten
URL-unsafe shipped ratings move the member set and which side is at fault.

Reuses 9b's recorded baseline: `studio=Columbia%20Pictures` → **43 items**
(`docs/research/plex-search-probe/README.md:129`, `:142`). Re-sent with the
space spelled `+` instead:

```
GET <plex-host>/library/sections/1/all?type=1&sort=titleSort&studio=Columbia+Pictures
    -> HTTP 200   totalSize=43
```

**43, identical to the `%20` baseline. The server folds `+` to a space
(Model F).** Decisive in both directions by construction: `studio` is a `str`
row, so the bare form is a *contains* match on both paths
(`docs/research/plex-search-probe/README.md:148`); had the server taken `+`
literally, no studio contains the substring `Columbia+Pictures` and the answer
would have been 0.

So premise P5 — "an unencoded `+` in a value reaches the matcher as a space" —
is **measured, not inferred**. The equivalence proof's partition stands as
written: six of the ten ratings move the member set, and the **new** side is the
one at fault.

### Probe P1 ride-along — is a comma-joined multi-value tag OR?

P1 ("`field=a,b` is OR over the values") rested on plexapi's construction
(`','.join(result)`, `library.py:1102`) plus the shipped family's live
membership agreeing — strong, but not the live-measurement standard P2 has.
`content_rating` is single-valued per item (`video.py:393`), so the two models
are maximally far apart: OR returns the union, AND returns nothing.

Baselines, from 9b: this library's ratings are numeric, `('17', 291)` and
`('16', 236)` its two commonest, and `contentRating=17` → **291** server-side
(`docs/research/plex-search-probe/README.md:161-163`, `:168-169`).

```
GET <plex-host>/library/sections/1/all?type=1&sort=titleSort&contentRating=17%2C16
    -> HTTP 200   totalSize=527
```

**527 = 291 + 236, exactly.** The union, to the item, with no drift in either
baseline. AND would have returned 0. **P1 is now measured at P2's standard.**

### How they were run

One short script on the host — it needs `.env` and the network, so it did not
run in the test container. Not committed, same arrangement as §4's: it read
`AUTOPOSTER_PLEX_TOKEN` from `.env`, sent the token as an `X-Plex-Token`
**header** so it never appeared in a URL or on a command line, sent
`X-Plex-Container-Size: 0` so the responses carried `totalSize` and zero item
payload, and wrapped each request class-name-only so a failure's message —
which quotes the tokenised URL — could not reach the output. The two lines
above are its entire output, verbatim apart from the host placeholder.
