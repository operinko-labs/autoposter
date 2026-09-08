# Kometa `imdb_search` — the constraint surface, transcribed from v2.4.8 source

Filed by roadmap row 257, which row 148's close filed as *"the prerequisite
nothing downstream can be honestly sized without"*. Before this file existed,
Kometa's `imdb_search` surface was transcribed nowhere in this repository:
`kometa-collections.md` is scoped to `oscars`/`imdb`/`content_rating_cs` and
greps to zero for `imdb_search`, and `.superpowers/kometa-v2.4.8/` holds three
data YAMLs, not Python source.

Ground truth for this document, in priority order:

1. **Kometa v2.4.8 Python source**, `modules/imdb.py` and `modules/builder.py`,
   unpacked from the pinned image
   `kometateam/kometa@sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a`
   into the session scratchpad (never into this repo). `/VERSION` in that image
   reads `2.4.8`. Every citation below is `<file>:<line>` in that tree, with the
   verbatim fragment quoted where it carries the point.
2. **This repository's own live IMDb walk**, `p8c-task-3-report.md` (71 probes,
   2026-08-25), for what has actually been observed on the wire — the only source
   here that is evidence about IMDb rather than about Kometa.
3. **This repository's shipped builder**,
   `src/autoposter/collections/builders/imdb_search.py` and
   `src/autoposter/collections/imdb_graphql.py`, for the mapping in §5.

**What this document is, and is not.** It is a transcription of what *Kometa*
sends. It is **not** proof that IMDb accepts any of it, and §6 explains exactly
why the two are not the same claim. Rows 258–265 stay probe-gated after this
file lands.

**To reproduce the extraction**

```sh
IMG="docker.io/kometateam/kometa@sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a"
CID=$(docker create --entrypoint sh "$IMG")
docker cp "$CID:/modules" <scratchpad>/kometa/modules
docker cp "$CID:/VERSION" <scratchpad>/kometa/VERSION
docker rm -f "$CID"
```

---

## 1. How one `imdb_search` becomes one request

Three files, three stages.

1. **Validation**, `builder.py:2386-2580`. `_imdb`'s `imdb_search` branch parses
   the operator's YAML dict into a flat `new_dictionary` keyed by the lowercased
   attribute name (`type.not`, `rating.gte`, …). An attribute not in
   `imdb.imdb_search_attributes` is a hard error — `builder.py:2406-2407`:

   ```python
   elif lower_method not in imdb.imdb_search_attributes:
       raise BuilderValidationError(f"{self.Type} Error: {method_name} {search_method} attribute not supported")
   ```

   A `None` value is a hard error one line earlier (`:2404-2405`, *"attribute is
   blank"*).

2. **Placement**, `imdb.py:610-725` (`IMDb._graphql_json`). The flat dict becomes
   the GraphQL variables. All 28 constraint objects are written by one helper,
   `check_constraint` (`imdb.py:617-641`), or by one of five hand-written blocks
   (award, ranked list, company, adult, and the always-on title type).

3. **Transport**, `imdb.py:724-725` and `:506-520`. A **persisted query** POST to
   `https://api.graphql.imdb.com/` with `x-imdb-client-name: imdb-web-next`. See
   §6.1 — this is the single most important caveat in this document.

`check_constraint`'s contract, which the whole of §2 is read through:

```python
def check_constraint(bases, mods, constraint, lower="", translation=None, range_name=None, obj_name=None):
```

- `bases` — the YAML attribute stem (`"genre"`), or a list of stems sharing one
  constraint object (`["rating", "votes"]`).
- `mods` — `(suffix, imdb_prefix)` pairs. `("any", "any")` means the YAML key
  `genre.any` becomes the GraphQL key `any` + `lower`. The empty suffix `""` is
  the bare key (`genre`).
- `lower` — the suffix appended to every generated key (`"GenreIds"` →
  `allGenreIds`, `anyGenreIds`, `excludeGenreIds`).
- `range_name` — when set, the mods build a nested range object under that name
  instead of sibling keys (`{"min": …, "max": …}`).
- `obj_name` — when set, each list element is wrapped (`["nm1"]` →
  `[{"nameId": "nm1"}]`).
- `translation` — a dict (alias → id), or a `(from, to)` tuple applied as a
  string replace to every element.

An attribute the operator did not write is **absent** from the payload, never
sent wide open (`imdb.py:628-629`, the `if full_attr in data` guard). The one
exception is `titleTypeConstraint`, which is always emitted (§2, row 2).

---

## 2. The 28 constraint families

Kometa's `imdb_search` accepts **75 YAML attributes** (`imdb.py:73-149`,
`imdb_search_attributes`), of which two — `limit` and `sort_by` — do not filter
(§3), leaving **73 filtering attributes across 28 GraphQL constraint objects**.

Read the "GraphQL" column as: object name, then its field(s) in the order the
YAML suffixes generate them.

| # | Kometa YAML key(s) | GraphQL constraint object → field(s) | Value shape | Kometa validation | Cite (`imdb.py` unless noted) |
|---|---|---|---|---|---|
| 1 | `title` | `titleTextConstraint.searchTerm` | one string | free text, no vocabulary | `:653`; `builder.py:2410-2411` |
| 2 | `type`, `type.not` | `titleTypeConstraint.anyTitleTypeIds`, `.excludeTitleTypeIds` | list of ids | 15-name table → id (`movie`, `tvSeries`, `tvEpisode`, `tvMiniSeries`, `tvMovie`, `tvSpecial`, `tvShort`, `short`, `videoGame`, `video`, `musicVideo`, `podcastSeries`, `podcastEpisode`) | `:647`, `:651`, `:172-186`; `builder.py:2412-2420` |
| 3 | `release.after`, `release.before` | `releaseDateConstraint.releaseDateRange.{start,end}` | `YYYY-MM-DD` strings | parsed as a date, re-emitted `%Y-%m-%d` | `:652`; `builder.py:2430-2438` |
| 4 | `rating.gte`, `rating.lte`, `votes.gte`, `votes.lte` | `userRatingsConstraint.aggregateRatingRange.{min,max}`, `.ratingsCountRange.{min,max}` | floats 0.1–10; ints ≥ 0 | `float` with `minimum=0.1, maximum=10`; `int` with `minimum=0` | `:654`; `builder.py:2439-2450` |
| 5 | `genre`, `genre.any`, `genre.not` | `genreConstraint.allGenreIds`, `.anyGenreIds`, `.excludeGenreIds` | list of IMDb genre spellings | 27-name table, lowercase key → IMDb's capitalisation | `:655`, `:187-218`; `builder.py:2451-2459` |
| 6 | `interests`, `.any`, `.not` | `interestConstraint.allInterestIds`, `.anyInterestIds`, `.excludeInterestIds` | list of `in\d+` ids | ~200-name alias table, else a raw `in\d+` match, else a hard error | `:656`, `:219-431`; `builder.py:2473-2485` |
| 7 | `topic`, `.any`, `.not` | `withTitleDataConstraint.allDataAvailable`, `.anyDataAvailable`, `.noDataAvailable` | list of enums | 11 fixed values: `ALTERNATE_VERSION`, `AWARD`, `BUSINESS_INFO`, `CRAZY_CREDIT`, `GOOF`, `LOCATION`, `PLOT`, `QUOTE`, `SOUNDTRACK`, `TECHNICAL`, `TRIVIA` | `:657`, `:432-444`; `builder.py:2421-2429` |
| 8 | `alternate_version`, `.any` | `alternateVersionMatchingConstraint.allAlternateVersionTextTerms`, `.anyAlternateVersionTextTerms` | list of free lowercase strings | none beyond lowercasing | `:658`; `builder.py:2528-2541` |
| 9 | `crazy_credit`, `.any` | `crazyCreditMatchingConstraint.allCrazyCreditTextTerms`, `.anyCrazyCreditTextTerms` | list of free lowercase strings | none beyond lowercasing | `:659`; `builder.py:2528-2541` |
| 10 | `location`, `.any` | `filmingLocationConstraint.allLocations`, `.anyLocations` | list of free lowercase strings | none beyond lowercasing | `:660`; `builder.py:2528-2541` |
| 11 | `goof`, `.any` | `goofMatchingConstraint.allGoofTextTerms`, `.anyGoofTextTerms` | list of free lowercase strings | none beyond lowercasing | `:661`; `builder.py:2528-2541` |
| 12 | `plot`, `.any` | `plotMatchingConstraint.allPlotTextTerms`, `.anyPlotTextTerms` | list of free lowercase strings | none beyond lowercasing | `:662`; `builder.py:2528-2541` |
| 13 | `quote`, `.any` | `quoteMatchingConstraint.allQuoteTextTerms`, `.anyQuoteTextTerms` | list of free lowercase strings | none beyond lowercasing | `:663`; `builder.py:2528-2541` |
| 14 | `soundtrack`, `.any` | `soundtrackMatchingConstraint.allSoundtrackTextTerms`, `.anySoundtrackTextTerms` | list of free lowercase strings | none beyond lowercasing | `:664`; `builder.py:2528-2541` |
| 15 | `trivia`, `.any` | `triviaMatchingConstraint.allTriviaTextTerms`, `.anyTriviaTextTerms` | list of free lowercase strings | none beyond lowercasing | `:665`; `builder.py:2528-2541` |
| 16 | `character` | `characterConstraint.anyCharacterNames` | list of free lowercase strings | none beyond lowercasing | `:710`; `builder.py:2528-2541` |
| 17 | `cast`, `.any`, `.not` | `titleCreditsConstraint.allCredits`, `.anyCredits`, `.excludeCredits` | list of `{"nameId": "nm…"}` objects | each element must match `nm\d+`, else a hard error | `:709`; `builder.py:2542-2551` |
| 18 | `series`, `series.not` | `episodicConstraint.anySeriesIds`, `.excludeSeriesIds` | list of `tt…` ids | each element must match `tt\d+` | `:693`; `builder.py:2552-2561` |
| 19 | `list`, `.any`, `.not` | `listConstraint.inAllLists`, `.inAnyList`, `.notInAnyList` | list of `ls…` ids | each element must match `ls\d+` | `:694`; `builder.py:2562-2571` |
| 20 | `country`, `.any`, `.not`, `.origin` | `originCountryConstraint.allCountries`, `.anyCountries`, `.excludeCountries`, `.anyPrimaryCountries` | list of 2-letter uppercase codes | length must be exactly 2, else a hard error; **no vocabulary is pinned** | `:706`; `builder.py:2519-2527` |
| 21 | `language`, `.any`, `.not`, `.primary` | `languageConstraint.allLanguages`, `.anyLanguages`, `.excludeLanguages`, `.anyPrimaryLanguages` | list of free lowercase strings | **none** — not even a length check | `:708`; `builder.py:2528-2541` |
| 22 | `keyword`, `.any`, `.not` | `keywordConstraint.allKeywords`, `.anyKeywords`, `.excludeKeywords` | list of lowercase strings, **spaces replaced by hyphens** | none; the space→hyphen rewrite is the whole normalisation | `:707`; `builder.py:2528-2541` |
| 23 | `content_rating` | `certificateConstraint.anyRegionCertificateRatings` | list of `{"region": "US", "rating": "PG-13"}` objects | `rating` required; `region` optional, must be 2 characters, **defaults to `US`** with a warning | `:705`; `builder.py:2499-2518` |
| 24 | `company` | `creditedCompanyConstraint.anyCompanyIds` | list of `co…` ids | 8 studio aliases expand to id lists (`disney` → six ids); otherwise a raw `co\d+` match, else a hard error | `:696-703`, `:445-454`; `builder.py:2486-2498` |
| 25 | `event`, `event.winning` | `awardConstraint.allEventNominations` | list of `{"eventId": "ev…"}`, optionally plus `"searchAwardCategoryId"` and, for `.winning`, `"winnerFilter": "WINNER_ONLY"` | 13 aliases (`oscar`, `emmy`, `bafta`, `cannes`, `razzie`, `oscar_picture`, `oscar_director`, …), else a raw `ev\d+` match, else a hard error | `:667-676`, `:455-467`; `builder.py:2460-2472` |
| 26 | `imdb_top`, `imdb_bottom`, `popularity.gte`, `popularity.lte` | `rankedTitleListConstraint.allRankedTitleLists` | list of `{"rankRange": {"min"/"max": int}, "rankedTitleListType": …}` with type `TOP_RATED_MOVIES`, `LOWEST_RATED_MOVIES` or `TITLE_METER` | `int` with `minimum=0` | `:678-691`; `builder.py:2449-2450` |
| 27 | `runtime.gte`, `runtime.lte` | `runtimeConstraint.runtimeRangeMinutes.{min,max}` | ints ≥ 0, **minutes** | `int` with `minimum=0` | `:711`; `builder.py:2449-2450` |
| 28 | `adult` | `explicitContentConstraint.explicitContentFilter` | the single string `INCLUDE_ADULT` | boolean; the object is emitted **only when true** | `:713-714`; `builder.py:2572-2574` |

Five of those are not written by `check_constraint` and are worth reading in
full.

**Row 2 — the always-on one.** `titleTypeConstraint` is written before any
`check_constraint` call and is sent even when the operator wrote no `type`
(`imdb.py:647`):

```python
out["titleTypeConstraint"] = {"anyTitleTypeIds": [title_type_options[t] for t in data["type"]] if "type" in data else []}
```

An empty `anyTitleTypeIds` list is therefore a normal payload for Kometa. This
repo never sends one — `search_constraints` always fills it from the library type.

**Row 25 — award.** `event` and `event.winning` merge into one list, and the same
event may appear twice with different filters (`imdb.py:667-676`). Two of the 13
aliases carry a category as well as an event: `oscar_picture` is `{"eventId":
"ev0000003", "searchAwardCategoryId": "bestPicture"}`.

**Row 26 — ranked lists.** Four YAML attributes collapse into one list of range
objects. `imdb_top: 250` becomes `{"rankRange": {"max": 250},
"rankedTitleListType": "TOP_RATED_MOVIES"}` — a *rank ceiling*, not a count — and
`popularity.gte`/`.lte` become a `TITLE_METER` range on IMDb's popularity rank
(`imdb.py:678-691`).

**Row 24 — company.** The eight aliases are one-to-many: `warner` expands to six
company ids, `disney` to six, `fox` to four (`imdb.py:445-454`).

**Row 23 — certificate.** The only family whose values are objects with a region,
and the only one with a silent default: a `content_rating` entry written as a
bare string becomes `{"region": "US", "rating": "<the string>"}`
(`builder.py:2502-2506`). A dict entry without `rating` is a hard error; a dict
entry whose `region` is not exactly two characters logs a warning and falls back
to `US` (`builder.py:2511-2515`).

### 2.1 Eight attributes Kometa accepts and never sends

`imdb_search_attributes` lists a `.not` variant for all eight text-matching
families — `alternate_version.not`, `crazy_credit.not`, `location.not`,
`goof.not`, `plot.not`, `quote.not`, `soundtrack.not`, `trivia.not`
(`imdb.py:94-117`) — and `builder.py:2528-2541` validates them like any other
list. But the corresponding `check_constraint` calls (`imdb.py:658-665`) declare
only `("", "all")` and `("any", "any")`:

```python
check_constraint("plot", [("", "all"), ("any", "any")], "plotMatchingConstraint", lower="PlotTextTerms")
```

So a Kometa operator can write `plot.not:` , see no error, and have it **silently
dropped** before the wire. Recorded here because it is exactly the class of
failure this project's own `extra="forbid"` params model exists to prevent, and
because anyone porting these eight families should not port the `.not` suffix as
if it worked.

---

## 3. The variables that are not constraints

| Variable | Source | Value |
|---|---|---|
| `locale` | fixed | `"en-US"` (`imdb.py:613`) |
| `first` | `limit` | `data["limit"]` when `0 < limit < 250`, else `250` (`imdb.py:611-614`). The operator-facing default is **100** (`builder.py:2390-2400`, `default=100, minimum=0`); `limit: 0` means "no limit" and yields `first: 250` |
| `sortBy` | `sort_by` | one of 8 enum values (below) |
| `sortOrder` | `sort_by` | `ASC` or `DESC`, from the suffix, uppercased (`imdb.py:648-649`) |
| `after` | paging | the previous page's `endCursor` (`imdb.py:786`) |

`sort_by` is written `<key>.<asc|desc>` and defaults to `popularity.asc`
(`imdb.py:643`). The eight keys (`imdb.py:150-159`):

| YAML key | GraphQL enum |
|---|---|
| `popularity` | `POPULARITY` |
| `title` | `TITLE_REGIONAL` |
| `rating` | `USER_RATING` |
| `votes` | `USER_RATING_COUNT` |
| `box_office` | `BOX_OFFICE_GROSS_DOMESTIC` |
| `runtime` | `RUNTIME` |
| `year` | `YEAR` |
| `release` | `RELEASE_DATE` |

**Paging.** 250 ids per page for a search (`imdb.py:744`), `total` read from
`data.advancedTitleSearch.total`, `endCursor` threaded into `variables.after` (`imdb.py:781`),
and the last page truncated to the remainder (`imdb.py:766-788`). Kometa applies
**no** page cap: `num_of_pages = math.ceil(limit / 250)` where `limit` becomes
`total` when the operator asked for none. This repo caps at `MAX_PAGES = 10`
(`imdb_graphql.py:96`) — 2,500 ids — with a warning at the cap.

**The "at least one constraint" rule.** Kometa's is a length test on the parsed
dict (`builder.py:2577-2580`):

```python
if len(new_dictionary) > 1:
    self.builders.append((method_name, new_dictionary))
else:
    raise BuilderValidationError(f"{self.Type} Error: {method_name} had no valid fields")
```

`limit` is always present, so `> 1` means "at least one other attribute". Note
what that admits: `sort_by` alone satisfies it, and so does `type` alone. This
repo's `_needs_at_least_one_constraint`
(`builders/imdb_search.py:227-249`) is deliberately stricter — it excludes both
`type` and `sort` from the filtering set, because an unconstrained advanced
search is `imdb_chart` with extra steps.

---

## 4. Kometa's validation model, and where it is weaker than this repo's

Kometa validates in three tiers, and the tier decides the failure mode:

1. **Closed vocabulary, hard error on a miss** — `type` (15), `genre` (27),
   `topic` (11), `sort_by` (16 = 8 × 2). An unknown value raises before any
   request.
2. **Pattern or alias, hard error on a miss** — `interests` (`in\d+`), `company`
   (`co\d+`), `event` (`ev\d+`), `cast` (`nm\d+`), `series` (`tt\d+`), `list`
   (`ls\d+`), `country` (exactly 2 characters). The *shape* is checked; the value
   is not.
3. **Nothing at all** — `title`, `keyword`, `language`, `character`,
   `content_rating`'s `rating`, and all eight text-matching families. Whatever the
   operator wrote goes to IMDb lowercased.

Tier 3 is where the risk lives, and this repo has already measured why: IMDb
validates a constraint's **shape** and not its **values** (probes P42 and P51 —
`anyTitleTypeIds: ["zzzNotAType"]` and `allGenreIds: ["ZzzNotAGenre"]` both answer
HTTP 200 with `total: 0` and no error;
`builders/imdb_search.py:20-27` is the module docstring that records it). So a
tier-3 typo is not an error, it is an **emptied collection** — and with
`sync_mode: sync` an emptied collection is a collection that had its members
removed.

Two vocabularies Kometa does pin are worth cross-checking against this repo's
live-proven ones:

- **Genres.** Kometa lists 27 (`imdb.py:187-218`). This repo's `GENRES`
  (`builders/imdb_search.py:66-72`) lists 28 — Kometa's 27 plus `Adult` — and
  every one of the 28 returned a non-zero `total` live on 2026-08-25. Kometa's set
  is a strict subset of a set proven real. **Neither source contradicts the
  other.**
- **Title types.** This repo pins three ids it needs (`movie`, `tvSeries`,
  `tvMiniSeries`, `builders/imdb_search.py:82-85`). Kometa's table names 15,
  including `tvEpisode`, which this repo deliberately excludes (9.5 million of
  IMDb's 31 million titles are episodes).
- **Sorts.** Both agree on `POPULARITY`, `USER_RATING`, `USER_RATING_COUNT`,
  `RELEASE_DATE` and `RUNTIME`. This repo's walk recorded `TITLE` and `BOX_OFFICE`
  as **rejected outright**; Kometa uses `TITLE_REGIONAL` and
  `BOX_OFFICE_GROSS_DOMESTIC`, which are different enum values and were never
  tried, plus `YEAR`, also never tried. **Three sort keys this project has not
  probed and Kometa believes in.**

---

## 5. The map to this repository

This repo ships **4** of Kometa's 28 families. The roadmap's rows 258–265 own
**8** more. **16** are owned by no row at all.

| # | Kometa constraint | This repo | Owner |
|---|---|---|---|
| 2 | `titleTypeConstraint` | **shipped** — always sent, from `ctx.library_type` via `TITLE_TYPE_IDS`; `search_constraints`, `builders/imdb_search.py:262` | — |
| 5 | `genreConstraint` | **shipped** — `genres` param → `allGenreIds`, `builders/imdb_search.py:142`, `:265` | — |
| 4 | `userRatingsConstraint` | **shipped** — `rating_gte`/`rating_lte` → `aggregateRatingRange`, `votes_gte` → `ratingsCountRange.min`; `:146-152`, `:267-276`. **`votes.lte` has no param** — Kometa sends `ratingsCountRange.max`, this repo cannot | partial gap, unowned |
| 3 | `releaseDateConstraint` | **shipped** — `released_after`/`released_before` → `releaseDateRange.{start,end}`, `:153-154`, `:279-287` | — |
| 27 | `runtimeConstraint` | missing | **row 258** |
| 23 | `certificateConstraint` | missing | **row 259** |
| 20 | `originCountryConstraint` | missing | **row 260** (which called it `countryConstraint`) |
| 21 | `languageConstraint` | missing | **row 261** |
| 22 | `keywordConstraint` | missing | **row 262** |
| 17 | `titleCreditsConstraint` | missing | **row 263** (which called it `creditConstraint`) |
| 25 | `awardConstraint` | missing | **row 264** |
| 19 | `listConstraint` | missing | **row 265** |
| 1 | `titleTextConstraint` | missing | none |
| 6 | `interestConstraint` | missing | none |
| 7 | `withTitleDataConstraint` | missing | none |
| 8 | `alternateVersionMatchingConstraint` | missing | none |
| 9 | `crazyCreditMatchingConstraint` | missing | none |
| 10 | `filmingLocationConstraint` | missing | none |
| 11 | `goofMatchingConstraint` | missing | none |
| 12 | `plotMatchingConstraint` | missing | none |
| 13 | `quoteMatchingConstraint` | missing | none |
| 14 | `soundtrackMatchingConstraint` | missing | none |
| 15 | `triviaMatchingConstraint` | missing | none |
| 16 | `characterConstraint` | missing | none |
| 18 | `episodicConstraint` | missing | none |
| 24 | `creditedCompanyConstraint` | missing | none |
| 26 | `rankedTitleListConstraint` | missing | none |
| 28 | `explicitContentConstraint` | missing | none |

### 5.1 The eight families the roadmap named, checked against Kometa

The row-148 recon marked its eight as *"unsourced — IMDb's form sections recalled
from the UI"* and asked that this transcription say plainly which of them are
real. The answer: **none is absent from Kometa, and two were misnamed.**

| Row | The name the roadmap used | Kometa's name | Verdict |
|---|---|---|---|
| 258 | `runtimeConstraint` | `runtimeConstraint`, range field `runtimeRangeMinutes` | **exists as named** |
| 259 | `certificateConstraint` | `certificateConstraint`, field `anyRegionCertificateRatings`, operator key `content_rating` | **exists as named**; the operator-facing key differs, and the region question is answered |
| 260 | `countryConstraint` | `originCountryConstraint` | **renamed** |
| 261 | `languageConstraint` | `languageConstraint` | **exists as named** |
| 262 | `keywordConstraint` | `keywordConstraint` | **exists as named** |
| 263 | `creditConstraint` | `titleCreditsConstraint`, operator key `cast`, elements `{"nameId": "nm…"}` | **renamed** |
| 264 | `awardConstraint` | `awardConstraint`, field `allEventNominations` | **exists as named** |
| 265 | (no name given — "list-membership") | `listConstraint`, fields `inAllLists`/`inAnyList`/`notInAnyList` | **exists**, now named |

Two sizing notes the transcription settles without a probe:

- **Row 263's second source is confirmed necessary.** Kometa refuses anything but
  an `nm\d+` id (`builder.py:2542-2551`), so Kometa's operators write ids too.
  Kometa is not the name→id resolver this project would need; it has none either.
- **Row 259's region unknown is answered** — region is a 2-letter code carried
  **per value**, paired with the rating in one object, defaulting to `US`. It is
  not a separate top-level field and not a constraint-wide setting.

---

## 6. What this document proves, and what it does not

### 6.1 Kometa's key names are an inference, not a schema reading

Kometa does not send a query text. It sends a **persisted query**
(`imdb.py:724-725`):

```python
return {"operationName": op, "variables": out, "extensions": {"persistedQuery": {"version": 1, "sha256Hash": sha}}}
```

and the hash is fetched at runtime from a GitHub raw file Kometa's team maintains
(`imdb.py:522-526`, `search_hash_url` at `:471` →
`Kometa-Team/IMDb-Hash/master/HASH`). Two consequences:

1. **The operation's variable definitions are not in Kometa's source.** Every
   constraint object above rides as a **top-level GraphQL variable**, not nested
   under a `constraints:` argument. This repo's `SEARCH_QUERY`
   (`imdb_graphql.py:146-152`) declares `$constraints:
   AdvancedTitleSearchConstraints!` and passes it as one argument. That Kometa's
   variable names are the *fields of `AdvancedTitleSearchConstraints`* is an
   inference from the four that overlap — a good one, but an inference.
2. **Kometa's transport cannot be copied.** Sending Kometa's persisted hash would
   couple this project to a third-party file that changes when IMDb's web client
   changes. The inline-query form this repo already uses is the right transport;
   only the *names* transfer.

### 6.2 The calibration: four families, two sources, zero disagreement

The four families this repo ships were named by probe-by-error-message against the
live endpoint on 2026-08-25 and each was proven by a non-zero `total`. Kometa
names all four identically, field for field:

| This repo, live-proven | Kometa | Kometa cite |
|---|---|---|
| `titleTypeConstraint.anyTitleTypeIds` | same | `:647` |
| `genreConstraint.allGenreIds` | same (plus `anyGenreIds`, `excludeGenreIds`) | `:655` |
| `userRatingsConstraint.aggregateRatingRange.{min,max}` | same | `:654` |
| `userRatingsConstraint.ratingsCountRange.{min,max}` | same | `:654` |
| `releaseDateConstraint.releaseDateRange.{start,end}` | same | `:652` |

Eight field names, two independent derivations, no conflict. That is the reason
to treat the other 24 as high-quality candidates — and it is still not evidence
about IMDb, because Kometa's set and this repo's set could both have been read
from the same web client.

### 6.3 What still needs a probe

Everything in §2 that this repo does not already send. Specifically: that the
constraint object exists under that name, that its fields take the shape recorded,
and — for every tier-2 and tier-3 family in §4 — that the *values* an operator
writes are ones IMDb recognises, because an unrecognised value is `total: 0` with
an HTTP 200 and a collection emptied in silence, not an error. Kometa pins no
vocabulary at all for `language`, `keyword`, `country` or `content_rating`
ratings, so the vocabulary sweeps rows 259–262 were sized for are **not**
short-cut by this transcription.

---

## 7. The standing caution, and how a probe should be run

**IMDb already marks this root as denied by entitlement for anonymous callers.**
Every `advancedTitleSearch` response carries, in its `extensions`:

```
entitlements.fields."Query.advancedTitleSearch": "DENY"
```

recorded in `p8c-task-3-report.md` §6 item 5. The 71 probes of 2026-08-25 were
answered anyway — the marker is advisory today — but it is an explicit statement
that the endpoint's owner does not consider this root open to us. **Every probe
session for rows 258–265 carries a live enforcement risk**, and the risk is
cumulative across sessions, not per-request. Deeper investment in this root is a
bet that the marker stays advisory.

Given that, a probe session for any of rows 258–265 should:

1. **Be read-only and one query.** One POST, one candidate constraint, `first: 1`.
   Never a sweep and a shape probe in the same session; never a second query "while
   we are here".
2. **Run from the pod**, through the service's own transport
   (`collections/imdb_graphql.py`), not from a laptop and not with a hand-rolled
   client — the endpoint, headers and error handling are already the ones
   production uses, and a different client is a different fingerprint.
3. **Assert only on `errors[].message` and `total`.** The probe's finding is
   whether the constraint name is recognised, not which titles matched.
4. **Print no title list, no id list, and no hostname** into any report. `total`
   and the error text are the whole result.
5. **Stop on the first entitlement or `FORBIDDEN` error** and report it rather
   than retrying, rate-limiting down, or varying the client name. A refusal is the
   finding.
6. **Record the outcome in the row's cell**, so the next session does not re-spend
   the same query.

A vocabulary sweep (rows 259–262) is a different and much larger commitment —
roughly 250 requests for country or certificate — and should be treated as its own
decision, not as the tail of a shape probe.

---

## 8. Open questions

1. **Sixteen families no roadmap row owns** (§5). `titleTextConstraint`,
   `interestConstraint`, `withTitleDataConstraint`, the eight text-matching
   families, `characterConstraint`, `episodicConstraint`,
   `creditedCompanyConstraint`, `rankedTitleListConstraint` and
   `explicitContentConstraint`. Whether any of them is worth a row is a decision
   this document does not make. `rankedTitleListConstraint` is the interesting one:
   it is how Kometa expresses "IMDb Top 250 **and** something else" in a single
   query, which the shipped `imdb_chart` builder cannot combine with anything.
2. **`votes.lte` is a gap inside a shipped family** (§5, row 4). Kometa sends
   `ratingsCountRange.max`; this repo has `votes_gte` and no `votes_lte`, so a
   "popular but not blockbuster" window cannot be expressed. It needs no probe —
   the field is inside a constraint object already proven live — and it is the
   cheapest `imdb_search` improvement identified anywhere in this document.
3. **Three unprobed sort keys** (§4): `TITLE_REGIONAL`,
   `BOX_OFFICE_GROSS_DOMESTIC` and `YEAR`. The live walk rejected `TITLE` and
   `BOX_OFFICE`; these are different enum values it never tried.
4. **Kometa's own `.not` bug** (§2.1) is reported nowhere upstream from this
   repository, and this document takes no action on it.
