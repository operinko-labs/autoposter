# The Common Sense grammar equivalence proof (phase 10a-2, Task 3)

`.superpowers/sdd/p10a-facts.md` Addendum 2, point 2 makes this proof the gate
on the Common Sense write-path port. The golden fixture
(`tests/fixtures/collections/golden_port.json`) records `updated_filters` --
the plexapi CALL SHAPE the port removes -- so the moment the port stops calling
plexapi that fixture stops covering the changed cell. What has to be graded
instead is whether the OLD query and the NEW one select the same items.

## The three files

| File | What it is |
| --- | --- |
| `plexapi_side.py` | plexapi 4.18.2's REAL `_buildSearchKey`, driven against a `LibrarySection` subclass whose four filter-metadata lookups are stubbed. Not a transcription. |
| `ours_side.py` | this engine's REAL `parse_filters(..., base="any")` + `build_search_url`. |
| `member_sets.py` | an independent decoder: both query strings into one predicate tree, evaluated over a fixture library. Imports nothing from this repository, and shares no helper with either driver -- so no single bug can reach both sides of the comparison. |

The gate is `tests/test_collection_cs_equivalence.py`. It loads all three by
PATH, the way 9b's gate loads its own driver
(`tests/test_collection_search_oracle.py:276`): `tests/oracle` is not a package,
deliberately, because `import tests.oracle.10a2` is a `SyntaxError`.

The bucket shapes are NOT a table in either driver. The gate walks
`buckets.derive_buckets` over the shipped
`assets/collections/content_rating_cs.json`, so a table edit cannot leave a
proof about the old table standing.

## The five premises

Each is named in `member_sets.py` with its citation, so a reviewer can reject a
premise rather than re-derive the whole thing.

| | Premise | Evidence |
| --- | --- | --- |
| P1 | `field=a,b` is OR over the values | **live**: `contentRating=17%2C16` → **527 = 291 + 236**, the union to the item; AND would have been 0 |
| P2 | `push`/`or=1`/`pop` groups, `or` is OR and `and` is AND | **live**: the same five subtitle variants, 442 under `any:` and 0 under `all:` |
| P3 | `sort`/`limit`/`includeGuids` decide what is *returned*, never what matches | source (`builder.py:4287-4289`, `library.py:1266`) |
| P4 | under `type=N`, `contentRating` and `<libtype>.contentRating` name one field | inference, with live corroboration: `show.network=126689` answered under `type=2` |
| P5 | an unencoded `+` reaches the matcher as a SPACE (the **server's** decoder) | **live**: `studio=Columbia+Pictures` → **43**, the `%20` baseline exactly |

P1's and P5's measurements are phase 10a-2 Task 3's own two read-only GETs,
recorded at `docs/research/plex-dynamic-probe/README.md` §5. P2's and P4's are
9b's (`docs/research/plex-search-probe/README.md`).

**What P4 is and is not.** The driver MEASURES which spelling plexapi *emits* --
`plexapi_side.py` runs plexapi's actual `_validateFilterField`, so whichever
field spelling plexapi produces for each library type is the one the proof
compares against, and the gate runs the show case under BOTH. That is a
measurement of the input, not of the premise: `member_sets._strip_libtype`
removes the difference before either side is evaluated, so the proof *cannot*
detect an inequivalence between the two spellings and assumes it away. The live
evidence that does exist is 9b's — `?type=2&sort=titleSort&show.network=126689`
answered on a real show section
(`docs/research/plex-search-probe/README.md:217`), and 9b already ships the
dotted spelling for this field (`filters.py:613`).

**What P5 does and does not decide.** It settles *which* six of the ten
URL-unsafe ratings move the member set, and *whose* side is at fault. It does
not decide whether the divergence exists — see "No decoding model rescues the
port" in Part 2.

## The verdict

**Equivalent for every URL-safe rating. A divergence for ten ratings the
shipped table carries.** Both halves are below.

> **Resolved in task 3.5.** The tag branch of `_arguments` now runs its resolved
> keys through the same `quote` the `str` branch two cases below it already
> used, so the ten are URL-safe on the wire and the equivalence covers the whole
> shipped table. Everything in Part 2 is the PRE-FIX measurement and is kept as
> the record of what was found; the three `xfail(strict=True)` tests it
> describes have retired themselves, exactly as they were built to. See "How the
> divergence was recorded, and how it retired" at the end of Part 2.
>
> The fix is a deliberate divergence from Kometa, which quotes only its own
> `str` branch (`builder.py:4246`), and it is still not byte-identical to
> plexapi: `quote`'s default `safe='/'` sends `gb/0%2B` where plexapi sends
> `gb%2F0%2B`. The two decode alike, which is what membership turns on. All
> seventeen 9b oracle configs stayed byte-identical, because every tag key 9b
> resolves is an opaque id over which `quote` is the identity.

## Part 1 -- the equivalence, every shipped bucket, both library types

Reproduce with `docker compose run --rm test python -` fed this script:

```python
import importlib.util
from pathlib import Path
from autoposter.collections.buckets import derive_buckets

def driver(name):
    spec = importlib.util.spec_from_file_location(
        name, Path("tests/oracle/10a2") / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ms, ours, plex = driver("member_sets"), driver("ours_side"), driver("plexapi_side")
CASES = [("movie", ours.MOVIE_RATINGS, "Movie", "contentRating"),
         ("show", ours.SHOW_RATINGS, "Show", "contentRating"),
         ("show", ours.SHOW_RATINGS, "Show", "show.contentRating")]
for libtype, ratings, ltype, fk in CASES:
    present = set(ratings)
    lib = [{"ratingKey": r, "contentRating": r} for r in ratings]
    lib.append({"ratingKey": "control", "contentRating": "ZZ-NOT-A-RATING"})
    print("### %s library, section field %r, ratings %s" % (ltype, fk, list(ratings)))
    print()
    for b in derive_buckets(present, ltype):
        if not b.values:
            print("bucket %-5s EMPTY -- no query is built (Addendum 3)" % b.key)
            continue
        old = plex.old_query(libtype, b.values, ratings, fk)
        new = ours.new_query(libtype, b.values, present)
        om, nm = ms.members(old, lib, libtype), ms.members(new, lib, libtype)
        print("bucket %-5s %s" % (b.key, list(b.values)))
        print("  old     %s" % old)
        print("  new     %s" % new)
        print("  members old=%s new=%s  EQUAL=%s" % (sorted(om), sorted(nm), om == nm))
    print()
```

Three things to read off it:

1. **The two grammars are not the same string.** plexapi comma-joins into one
   parameter (`contentRating=R%2CTV-MA`); this engine emits a
   `push`/`or=1`/`pop` group. That difference is what claim C2 is about, and the
   gate pins it so a change that accidentally made the two identical would have
   to say so.
2. **The field spellings differ in the middle case** -- plexapi emits a bare
   `contentRating` where this engine emits `show.contentRating` -- and the
   members still agree. That is the *emitted spelling* measured and both run;
   their semantic equivalence remains premise P4, which `_strip_libtype`
   assumes rather than tests (see "The five premises" above).
3. **Buckets 15 and 16 (movies) and 7 through 12 (shows) resolve empty.**
   Neither grammar is asked to build a query for them, here or in production
   (`reconcile.py:669`). Addendum 3's semantics, walked rather than described.

```
### Movie library, section field 'contentRating', ratings ['G', 'PG', 'PG-13', 'R', 'NC-17', 'NR', 'Unrated', 'TV-MA']

bucket 1     ['G']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=G
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=G&pop=1
  members old=['G'] new=['G']  EQUAL=True
bucket 2     ['G']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=G
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=G&pop=1
  members old=['G'] new=['G']  EQUAL=True
bucket 3     ['G']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=G
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=G&pop=1
  members old=['G'] new=['G']  EQUAL=True
bucket 4     ['G']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=G
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=G&pop=1
  members old=['G'] new=['G']  EQUAL=True
bucket 5     ['G']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=G
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=G&pop=1
  members old=['G'] new=['G']  EQUAL=True
bucket 6     ['G']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=G
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=G&pop=1
  members old=['G'] new=['G']  EQUAL=True
bucket 7     ['PG']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=PG
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=PG&pop=1
  members old=['PG'] new=['PG']  EQUAL=True
bucket 8     ['PG']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=PG
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=PG&pop=1
  members old=['PG'] new=['PG']  EQUAL=True
bucket 9     ['PG']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=PG
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=PG&pop=1
  members old=['PG'] new=['PG']  EQUAL=True
bucket 10    ['PG']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=PG
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=PG&pop=1
  members old=['PG'] new=['PG']  EQUAL=True
bucket 11    ['PG']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=PG
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=PG&pop=1
  members old=['PG'] new=['PG']  EQUAL=True
bucket 12    ['PG']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=PG
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=PG&pop=1
  members old=['PG'] new=['PG']  EQUAL=True
bucket 13    ['PG-13']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=PG-13
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=PG-13&pop=1
  members old=['PG-13'] new=['PG-13']  EQUAL=True
bucket 14    ['PG-13']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=PG-13
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=PG-13&pop=1
  members old=['PG-13'] new=['PG-13']  EQUAL=True
bucket 15    EMPTY -- no query is built (Addendum 3)
bucket 16    EMPTY -- no query is built (Addendum 3)
bucket 17    ['R', 'TV-MA']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=R%2CTV-MA
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=R&or=1&contentRating=TV-MA&pop=1
  members old=['R', 'TV-MA'] new=['R', 'TV-MA']  EQUAL=True
bucket 18    ['NC-17', 'R', 'TV-MA']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=NC-17%2CR%2CTV-MA
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=NC-17&or=1&contentRating=R&or=1&contentRating=TV-MA&pop=1
  members old=['NC-17', 'R', 'TV-MA'] new=['NC-17', 'R', 'TV-MA']  EQUAL=True
bucket other ['NR', 'Unrated']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=NR%2CUnrated
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=NR&or=1&contentRating=Unrated&pop=1
  members old=['NR', 'Unrated'] new=['NR', 'Unrated']  EQUAL=True

### Show library, section field 'contentRating', ratings ['TV-Y', 'TV-G', 'TV-14', 'TV-MA', 'NR', '13']

bucket 1     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 2     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 3     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 4     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 5     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 6     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 7     EMPTY -- no query is built (Addendum 3)
bucket 8     EMPTY -- no query is built (Addendum 3)
bucket 9     EMPTY -- no query is built (Addendum 3)
bucket 10    EMPTY -- no query is built (Addendum 3)
bucket 11    EMPTY -- no query is built (Addendum 3)
bucket 12    EMPTY -- no query is built (Addendum 3)
bucket 13    ['13']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=13
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=13&pop=1
  members old=['13'] new=['13']  EQUAL=True
bucket 14    ['TV-14']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-14
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-14&pop=1
  members old=['TV-14'] new=['TV-14']  EQUAL=True
bucket 15    ['TV-14']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-14
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-14&pop=1
  members old=['TV-14'] new=['TV-14']  EQUAL=True
bucket 16    ['TV-14']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-14
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-14&pop=1
  members old=['TV-14'] new=['TV-14']  EQUAL=True
bucket 17    ['TV-14', 'TV-MA']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-14%2CTV-MA
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-14&or=1&show.contentRating=TV-MA&pop=1
  members old=['TV-14', 'TV-MA'] new=['TV-14', 'TV-MA']  EQUAL=True
bucket 18    ['TV-MA']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=TV-MA
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-MA&pop=1
  members old=['TV-MA'] new=['TV-MA']  EQUAL=True
bucket other ['NR']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&contentRating=NR
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=NR&pop=1
  members old=['NR'] new=['NR']  EQUAL=True

### Show library, section field 'show.contentRating', ratings ['TV-Y', 'TV-G', 'TV-14', 'TV-MA', 'NR', '13']

bucket 1     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 2     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 3     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 4     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 5     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 6     ['TV-G', 'TV-Y']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-G%2CTV-Y
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-G&or=1&show.contentRating=TV-Y&pop=1
  members old=['TV-G', 'TV-Y'] new=['TV-G', 'TV-Y']  EQUAL=True
bucket 7     EMPTY -- no query is built (Addendum 3)
bucket 8     EMPTY -- no query is built (Addendum 3)
bucket 9     EMPTY -- no query is built (Addendum 3)
bucket 10    EMPTY -- no query is built (Addendum 3)
bucket 11    EMPTY -- no query is built (Addendum 3)
bucket 12    EMPTY -- no query is built (Addendum 3)
bucket 13    ['13']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=13
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=13&pop=1
  members old=['13'] new=['13']  EQUAL=True
bucket 14    ['TV-14']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-14
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-14&pop=1
  members old=['TV-14'] new=['TV-14']  EQUAL=True
bucket 15    ['TV-14']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-14
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-14&pop=1
  members old=['TV-14'] new=['TV-14']  EQUAL=True
bucket 16    ['TV-14']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-14
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-14&pop=1
  members old=['TV-14'] new=['TV-14']  EQUAL=True
bucket 17    ['TV-14', 'TV-MA']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-14%2CTV-MA
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-14&or=1&show.contentRating=TV-MA&pop=1
  members old=['TV-14', 'TV-MA'] new=['TV-14', 'TV-MA']  EQUAL=True
bucket 18    ['TV-MA']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=TV-MA
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=TV-MA&pop=1
  members old=['TV-MA'] new=['TV-MA']  EQUAL=True
bucket other ['NR']
  old     ?includeGuids=1&sort=show.originallyAvailableAt%3Adesc&type=2&show.contentRating=NR
  new     ?type=2&sort=originallyAvailableAt%3Adesc&push=1&show.contentRating=NR&pop=1
  members old=['NR'] new=['NR']  EQUAL=True
```

## Part 2 -- the divergence

*As measured before the task-3.5 fix. Every query string and member set printed
in this part is the pre-fix output; the boxed note under "The verdict" says what
changed and what did not.*

`build_search_url` inserted a resolved TAG value into the query **raw**; the
`str` branch two cases below it called `quote`, the tag branch did not. That never mattered in 9b because every tag key it
resolved was an opaque numeric id (`5`, `1138`, `es-419`). `content_rating` is
different: it is the one tag type whose Plex key IS its title, so the rating
TEXT reaches the query string. plexapi, by contrast, runs the value through
`urlencode` (`library.py:1103`).

The shipped table carries ten ratings with a space, a `+` or an `&`:

```
URL-unsafe values in the shipped table: 10

'12+'
  old  ...contentRating=12%2B
  new  ...push=1&contentRating=12+&pop=1
  members old=['12+'] new=[]  EQUAL=False
'G - All Ages'
  old  ...contentRating=G+-+All+Ages
  new  ...push=1&contentRating=G - All Ages&pop=1
  members old=['G - All Ages'] new=['G - All Ages']  EQUAL=True
'PG - Children'
  old  ...contentRating=PG+-+Children
  new  ...push=1&contentRating=PG - Children&pop=1
  members old=['PG - Children'] new=['PG - Children']  EQUAL=True
'PG-13 - Teens 13 or older'
  old  ...contentRating=PG-13+-+Teens+13+or+older
  new  ...push=1&contentRating=PG-13 - Teens 13 or older&pop=1
  members old=['PG-13 - Teens 13 or older'] new=['PG-13 - Teens 13 or older']  EQUAL=True
'R - 17+ (violence & profanity)'
  old  ...contentRating=R+-+17%2B+%28violence+%26+profanity%29
  new  ...push=1&contentRating=R - 17+ (violence & profanity)&pop=1
  members old=['R - 17+ (violence & profanity)'] new=[]  EQUAL=False
'R+ - Mild Nudity'
  old  ...contentRating=R%2B+-+Mild+Nudity
  new  ...push=1&contentRating=R+ - Mild Nudity&pop=1
  members old=['R+ - Mild Nudity'] new=[]  EQUAL=False
'Rx - Hentai'
  old  ...contentRating=Rx+-+Hentai
  new  ...push=1&contentRating=Rx - Hentai&pop=1
  members old=['Rx - Hentai'] new=['Rx - Hentai']  EQUAL=True
'gb/0+'
  old  ...contentRating=gb%2F0%2B
  new  ...push=1&contentRating=gb/0+&pop=1
  members old=['gb/0+'] new=[]  EQUAL=False
'gb/14+'
  old  ...contentRating=gb%2F14%2B
  new  ...push=1&contentRating=gb/14+&pop=1
  members old=['gb/14+'] new=[]  EQUAL=False
'gb/9+'
  old  ...contentRating=gb%2F9%2B
  new  ...push=1&contentRating=gb/9+&pop=1
  members old=['gb/9+'] new=[]  EQUAL=False
```

Six of the ten move the member set:

- `+` decodes as a SPACE, so `12+` is searched for as `12 ` and matches nothing
  (`12+`, `gb/0+`, `gb/9+`, `gb/14+`, `R+ - Mild Nudity`). **Measured** at the
  live server, probe P5: `studio=Columbia+Pictures` returns the same 43 items as
  the recorded `studio=Columbia%20Pictures`
  (`docs/research/plex-dynamic-probe/README.md` §5).
- `&` ENDS the parameter, so `R - 17+ (violence & profanity)` becomes a
  truncated `contentRating=R - 17  (violence ` plus a junk parameter
  ` profanity)`. In a single-value bucket the two are ANDed into the implicit
  group and the query selects nothing at all; in a real, mixed bucket they land
  in the `or=1` group and the junk term is simply never true — see the blast
  radius below.

The remaining four carry a space only. A literal space is not a legal URL
character, but a lenient parser reads it back unchanged, so the member set
survives; they are pinned on the bytes assertion alone.

`/` is fine: plexapi escapes it to `%2F` and this engine sends it raw, and both
decode to `/`. So `gb/U`, `no/A` and their kin are equivalent.

### No decoding model rescues the port

P5 is measured, so the partition above is the real one. But it is worth
recording that the *finding* never depended on it, because that is the strongest
form of it and the one Task 4 should act on:

| | Model F (`+` → space; measured, and what this proof assumes) | Model R (`+` literal) |
| --- | --- | --- |
| old `12%2B` | `12+` ✅ | `12+` ✅ |
| new `12+` | `12 ` ❌ | `12+` ✅ |
| old `G+-+All+Ages` | `G - All Ages` ✅ | `G+-+All+Ages` ❌ |
| new `G - All Ages` | `G - All Ages` ✅ | `G - All Ages` ✅ |
| new `…&…` | truncates ❌ | truncates ❌ |

Under Model R six ratings *still* move — the four space-only ones plus
`R+ - Mild Nudity` and `R - 17+ (violence & profanity)`, all of which carry a
space — except that the **old, shipped** side is the one at fault. **There is no
decoding model under which the two grammars agree on all ten**, and the `&` row
is URL syntax rather than server interpretation, so it holds in both columns.

The fix is premise-independent for the same reason: `quote()` emits `%2B`,
`%20` and `%26`, which decode identically under either model.

### The blast radius: a silent SUBSET, not an empty collection

The single-value shape above is the *rare* one. Every one of the ten unsafe
ratings co-occurs with safe ones in the shipped table:

```
key 17  unsafe ['gb/14+', 'R - 17+ (violence & profanity)']  alongside ['gb/18','gb/15','TV-14','R','TVMA','TV-MA', ...]
key 18  unsafe ['R - 17+ (violence & profanity)','R+ - Mild Nudity','Rx - Hentai']  alongside ['gb/18','MA-17','TVMA','TV-MA','R','NC-17', ...]
key 1   unsafe ['gb/0+','G - All Ages']  alongside ['gb/U','G','TV-G','TV-Y', ...]
```

In a real bucket those terms sit in an **OR** group (`or=1` present), so a
broken term does not empty the collection — the surviving terms still match and
the collection ships **plausible but short**. Bucket `17` against a vocabulary
holding both, verbatim:

```
bucket 17 values ['R', 'R - 17+ (violence & profanity)', 'TV-14', 'TV-MA', 'gb/14+']
  old     ?includeGuids=1&sort=movie.originallyAvailableAt%3Adesc&type=1&contentRating=R%2CR+-+17%2B+%28violence+%26+profanity%29%2CTV-14%2CTV-MA%2Cgb%2F14%2B
  new     ?type=1&sort=originallyAvailableAt%3Adesc&push=1&contentRating=R&or=1&contentRating=R - 17+ (violence & profanity)&or=1&contentRating=TV-14&or=1&contentRating=TV-MA&or=1&contentRating=gb/14+&pop=1
  members old=['R', 'R - 17+ (violence & profanity)', 'TV-14', 'TV-MA', 'gb/14+']
  members new=['R', 'TV-14', 'TV-MA']
  EQUAL=False  SUBSET=True  EMPTY=False
  lost    ['R - 17+ (violence & profanity)', 'gb/14+']
```

Three of five survived. A strict, non-empty subset missing exactly the two
unsafe values, with the injected ` profanity)` term OR'd into the group and
simply never true. `test_a_mixed_bucket_degrades_to_a_silent_subset` asserted
every line of that, and was deleted by the fix it existed to force.

That is the worse class. An empty smart collection is at least noticeable; a
plausible-but-short one is precisely the "silent wrongness" `search_url.py`'s own
module docstring names as the risk the whole phase sits on. It is also what the
migration paragraph has to describe, because it is what an operator would get.

**Which libraries.** Only those whose `contentRating` vocabulary carries one of
the ten — `derive_buckets` includes an addon only when the library actually
holds it. The `+` group (`12+`, `gb/0+`, `gb/9+`, `gb/14+`) is the realistic one
for a European library; `R+ - Mild Nudity` / `Rx - Hentai` /
`R - 17+ (violence & profanity)` are MyAnimeList-style and would show up in an
anime library.

**Likely latent on this server.** 9b's probe recorded this production library's
22 content ratings as numeric age values — `17`, `16`, `13`, `15`, `14` the
commonest (`docs/research/plex-search-probe/README.md:161-163`) — none of which
is URL-unsafe. So the defect is very probably latent *here*, and the blast
radius above is an inference from the shipped table rather than a measurement of
this operator's libraries. One read-only `listFilterChoices("contentRating")`
per section would settle whether it is urgent or latent; it was not run.

**The junk parameter is modelled, not measured.** This proof treats the injected
` profanity)` as a benign always-false term. A real server might instead reject
an unknown filter field outright. Either outcome is a divergence, so the verdict
is unaffected — but the specific sentence about what happens to it is a model
output.

### How the divergence was recorded, and how it retired

Three `xfail(strict=True)` tests at the bottom of the gate:

| Test | Cases | What it pins |
| --- | --- | --- |
| `test_the_new_grammar_encodes_every_shipped_rating` | 10 | the bytes |
| `test_a_plus_or_ampersand_rating_still_selects_the_same_items` | 6 | the meaning, single-value bucket |
| `test_a_mixed_bucket_selects_the_same_items_through_both_grammars` | 1 | the meaning, the realistic mixed bucket |

Strict was the point: the day the encoding was fixed they would XPASS, strict
would turn the XPASS into a failure, and whoever fixed it had to delete the
markers -- at which moment the equivalence above would cover the whole shipped
table instead of the URL-safe part of it.

`test_a_mixed_bucket_degrades_to_a_silent_subset` was deliberately **not**
marked: it asserted the subset shape as it stood, and it would red on the same
fix, which is the same forcing function by another route. It was to be deleted
with the markers.

**That is what happened, in task 3.5.** All seventeen strict cases XPASSed, the
unmarked one went red -- `18 failed, 18 passed`, recorded in
`.superpowers/run-t35-equiv-xpass.log` -- and the three markers were deleted,
the fourth test with them. Its
absolute pin -- the old query selects exactly the bucket, and never the control
item -- moved into the mixed-bucket test rather than being lost, so that test
still cannot pass for the wrong reason. The seventeen cases are now ordinary
passing coverage of the ten ratings.

## Why the proof can fail

`old == new` on its own passes whenever both sides are wrong the same way, so
three separate things guard it.

**The absolute assertion sits above the relative one.** Every case first asserts
that the OLD query selects exactly the bucket's own ratings, and that the
control item -- one carrying a rating no bucket claims -- is never selected.

**Seven literal member sets** are written by hand in the gate off the shipped
table, deriving nothing from `bucket.values`.

**A negative control for the comparison itself.**
`test_an_all_base_query_diverges_and_the_machinery_says_so` builds the same
bucket through the same real code with `base="all"`, which renders
`f=a&and=1&f=b`; no item holds two content ratings, so it selects nothing, and
the gate asserts the comparison NOTICES. That is the 9b probe's measured
442-under-`any` versus 0-under-`all` (premise P2) reproduced in the evaluator.

### The three mutations, and what each one reds

| | Mutation to `member_sets.py` | Result |
| --- | --- | --- |
| A | `_decode_values` stops splitting on the comma (P1 off) | 10 failed, 8 passed -- every multi-value bucket, on the OLD side |
| B | `_matches` makes every group an `all()` (P2 off) | 7 failed, 11 passed -- every multi-value bucket, on the NEW side, the mirror image |
| C | the `Term` branch returns `True` (the negative control) | 13 failed, 5 passed |

Mutation C is the one worth reading twice. With every predicate answering
`True`, both sides select the entire fixture library including the control item
-- so the RELATIVE assertion still holds and would have passed:

```
bucket 17 values      : ('R', 'TV-MA')
old_members           : ['G', 'NC-17', 'NR', 'PG', 'PG-13', 'R', 'TV-MA', 'Unrated', 'control']
new_members           : ['G', 'NC-17', 'NR', 'PG', 'PG-13', 'R', 'TV-MA', 'Unrated', 'control']
RELATIVE  old==new    : True <- would still have PASSED
ABSOLUTE  old==values : False <- this is what caught it
control leaked in     : True
```

That asymmetry is the whole reason the absolute assertion sits above the
relative one. A proof that cannot fail on an always-true predicate is not a
proof.

## The vocabularies

`MOVIE_RATINGS` and `SHOW_RATINGS` in `ours_side.py` are chosen so that all five
bucket shapes are reachable across the cases, which
`test_every_bucket_shape_was_exercised` asserts as a set equality rather than
leaving to inspection:

| Shape | Where |
| --- | --- |
| the bucket's own key, alone | shows, bucket `13` |
| key absent, one addon present | movies, bucket `1` (`G`) |
| key absent, several addons present | movies `17`; shows `1` |
| matches nothing (Addendum 3) | movies `15`, `16`; shows `7`-`12` |
| the `other` complement | movies (`NR`, `Unrated`); shows (`NR`) |

`13` is in `SHOW_RATINGS` for exactly one reason: every bucket key from `1` to
`18` has addons overlapping the common English ratings, so a vocabulary of plain
age words never reaches `derive_buckets`'s `[key] if key in present` branch
(`buckets.py:51`). Against this show vocabulary bucket `13`'s addons are all
absent, so its filter is its own key and nothing else.

## Scrub

There is no host, no token and no `X-Plex` header anywhere in this file or in
the drivers. The fake section key is `42`, and every query string is taken from
`?` onward.
