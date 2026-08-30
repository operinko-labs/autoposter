# TMDb ISO→name tables: provenance, the library's codes, and the region/continent join

Rows 196 (country names), 190 (language names) and 204's display-title half
share one absence: no ISO→name table exists anywhere in the tree, and Kometa's
own pack files carry none. This document is the fetched evidence that closes
that absence, and the measurement that gates the two location packs.

It is the committed output of Task 1 of
`docs/superpowers/plans/2026-08-31-location-names.md`. Its §Decisions rows
`D1`–`D6` are what Task 3's `CONFIRMED-BY-T1` block re-validates before the
pack transcriptions may flip READY.

Nothing here is recalled. Every name, count and hash below comes from bytes
fetched or already on disk, captured by the scripts reproduced verbatim in the
last section.

---

## 1. Provenance

| source | endpoint / file | date | HTTP | bytes | entries | sha256 |
| --- | --- | --- | --- | --- | --- | --- |
| TMDb configuration | `/configuration/countries` | 2026-08-30 | 200 | 18835 | 251 | `fb4609a1fb14d5b77aed0b65496c05f4c1d0018782b1c91cbc4c9f6bdf97d9e4` |
| TMDb configuration | `/configuration/languages` | 2026-08-30 | 200 | 11133 | 187 | `6e23ef1a9fcfb57aa2ac0838b836c9db21be6ee3b09b5bd9e5b57b32a46b1d60` |
| Kometa v2.4.8 (already fetched) | `.superpowers/kometa-v2.4.8/region.yml` | recorded at `p-prefetch-upstream.md:340-362` | — | 11163 | 346 distinct member strings | `9409fee72b78ddfefc0c77075557a9ce986e6f20830c3dae5e3e273f012ba60a` |
| Kometa v2.4.8 (already fetched) | `.superpowers/kometa-v2.4.8/continent.yml` | recorded at `p-prefetch-upstream.md:340-362` | — | 10883 | 330 distinct member strings | `fbc20666618fdd6728ea38766358fa2e559003eea3c8ebe7cb695527f3a40e05` |

The two upstream hashes match the prefixes recorded by the prefetch phase
(`9409fee7…`, `fbc20666…`) exactly — re-verified against the bytes on disk for
this measurement, not assumed from the record.

The raw fetched bytes live at `.superpowers/sdd/p-locnames-countries.json` and
`.superpowers/sdd/p-locnames-languages.json` (gitignored, kept). They are the
generation source for `src/autoposter/collections/iso_names.py`, which Task 2
emits **by script** from these bytes; that module is the committed derivation,
and these files are what it is regenerated from.

Both fetches were made once, from inside the `plocn1` test container, with the
read token taken from `/app/.env`'s `AUTOPOSTER_TMDB_TOKEN` (plan Global
Constraint 4). The token appears in no output, no log and no tracked file: the
URLs carry no key (the credential rides in an `Authorization` header) and
nothing in the probe prints headers.

The fetch record, `.superpowers/run-plocn-fetch.log`, verbatim:

```
countries: HTTP 200, 18835 bytes, 251 entries, sha256 fb4609a1fb14d5b77aed0b65496c05f4c1d0018782b1c91cbc4c9f6bdf97d9e4
languages: HTTP 200, 11133 bytes, 187 entries, sha256 6e23ef1a9fcfb57aa2ac0838b836c9db21be6ee3b09b5bd9e5b57b32a46b1d60
```

The sha256 values above were recomputed on the host from the saved files and
agree with the container's, so the bytes that landed on disk are the bytes that
came back.

A shape assertion over the saved files (`iso_3166_1` + `english_name` on every
country row, `iso_639_1` + `english_name` on every language row) passed for all
251 and all 187 entries.

### A note on the "647 member strings" in the facts file

`p-locnames-facts.md` C2 describes upstream's tables as 647 member strings.
Measured against the bytes: that is the count of **addon member values** in the
two files (323 in `region.yml` + 324 in `continent.yml`). Counting every
member entry, including the `include:` lists and the addon group keys, gives
703; counting distinct strings gives 346 (region) and 330 (continent), whose
union is 351. The join below is measured against the **distinct member
universe** of each file separately — group keys included, because a group key
is itself a matchable member name upstream.

---

## 2. The library's codes

**The probe returned no rows.** `.superpowers/sdd/p-locnames-db.txt` is empty:
zero `ORIGIN` lines, zero `LANG` lines.

That is not a probe failure, and establishing which it was mattered enough to
be checked rather than assumed. A sentinel `print` appended to the same script
came back (`SENTINEL-SCRIPT-REACHED-END`), so the script reached the pod, the
connection opened, both queries executed, and both returned zero rows. A
read-only diagnostic block was then appended to the same probe for one run
(the probe was restored to its verbatim form immediately afterwards; both the
verbatim script and the diagnostic block are reproduced in section 5). Its
output, `.superpowers/sdd/p-locnames-db-diagnostic.txt`, verbatim:

```
DIAG	media_items rows	15821
DIAG	item_facts rows	13590
DIAG	item_facts joined to media_items	13590
DIAG	origin_country NOT NULL	13590
DIAG	origin_country non-empty array	0
DIAG	original_language NOT NULL	0
DIAGKIND	episode	12604
DIAGKIND	movie	1961
DIAGKIND	season	965
DIAGKIND	show	291
DIAGCOL	id
DIAGCOL	item_id
DIAGCOL	critic_rating
DIAGCOL	audience_rating
DIAGCOL	content_rating
DIAGCOL	genres
DIAGCOL	studio
DIAGCOL	originally_available
DIAGCOL	sources
DIAGCOL	fetched_at
DIAGCOL	updated_at
DIAGCOL	tmdb_origin_country
DIAGCOL	tmdb_original_language
DIAGCOL	tmdb_collection_id
SENTINEL-SCRIPT-REACHED-END
```

What that says, precisely:

- The deployed schema **has** all three TMDb fact columns
  (`tmdb_origin_country`, `tmdb_original_language`, `tmdb_collection_id`) — a
  missing column would have raised `UndefinedColumnError` instead of returning
  an empty result.
- `tmdb_origin_country` is non-NULL on all 13590 `item_facts` rows, and every
  one of those arrays is **empty** (`[]`). The column is written; the values
  are not.
- `tmdb_original_language` is NULL on all 13590 rows.
- The library itself is populated: 15821 media items (1961 of them movies,
  which is the kind the two location packs serve) with facts rows for 13590.

So the TMDb enrichment that fills these two columns has not run against
production. **There are zero stored origin-country codes and zero stored
original-language codes to join.** The library-side measurement D3/D4 was
written for has an empty denominator, and this is the honest reading of the
production database as of 2026-08-30, not an inference.

### Why the columns are empty: sweep timing, measured

"The enrichment has not run" reads equally well as a pipeline bug, and the
difference decides whether the packs ever converge. So it was measured rather
than argued. One further read-only aggregate against the same production
database (`.superpowers/sdd/p-locnames-db2.txt`, script in section 5), run
2026-08-30 06:59:45Z:

```
AGG	now()	2026-08-30 06:59:45.947565+00:00
AGG	item_facts rows	13590
AGG	rows with ANY tmdb-sourced field	12458
AGG	rows with sources key tmdb_origin_country	0
AGG	rows with sources key tmdb_original_language	0
AGG	rows with sources key tmdb_collection_id	0
AGG	rows with non-empty sources	13590
AGG	max(fetched_at) overall	2026-08-28 14:00:53.302242+00:00
AGG	max(fetched_at) tmdb-sourced rows	2026-08-28 14:00:53.302242+00:00
AGG	max(updated_at) overall	2026-08-28 14:00:53.302242+00:00
AGG	min(fetched_at) overall	2026-08-23 21:50:16.919919+00:00
AGG	max(facts_attempted_at) media_items	None
AGG	media_items with facts_attempted_at NOT NULL	0
AGG	movie-kind facts rows	1961
AGG	max(fetched_at) movie-kind facts rows	2026-08-28 14:00:53.302242+00:00
AGG	item_facts rows fetched in last 7 days	13590
AGG	item_facts rows fetched since 2026-08-29	0
SRC	audience_rating	tmdb	12452
SRC	critic_rating	imdb	11728
SRC	genres	tmdb	2252
SRC	originally_available	tmdb	2252
SRC	studio	tmdb	2236
SRC	content_rating	mdb_commonsense	1801
SENTINEL-SCRIPT-REACHED-END
```

A note on the query shape, because the obvious form is wrong here:
`item_facts.sources` is a JSONB **object** mapping *field name → provider*
(`db/models.py:243`, built at `facts/gather.py:80` and
`facts/tmdb_facts.py:119-129`), not a list of providers. So `sources ? 'tmdb'`
tests keys and would return zero on a perfectly healthy row; the provider is the
**value**, which is what the `jsonb_each_text` aggregate above reads.

**The verdict is sweep timing, and the writer is provably healthy.** Three
independent facts, all above:

- **No gather has written anything since Phase A deployed.** The newest write of
  any kind to `item_facts` is `2026-08-28 14:00:53Z` — `max(fetched_at)` and
  `max(updated_at)` agree to the microsecond, and **0** rows were fetched on or
  after 2026-08-29. Phase A — the commit that first reads `origin_country`,
  `original_language` and the collection id off the TMDb payload and persists
  them (`253519e`, `2026-08-29T19:21:58+03:00` = `16:21Z`, on `main`) — landed
  **26 hours after** the last write (`26:21:05`, from the two timestamps above). Deploys here are Flux-automated,
  so production has been running Phase A code since roughly `2026-08-29 16:30Z`;
  the deployed schema confirms the rollout independently (all three `tmdb_*`
  columns present in the `DIAGCOL` list above, and `media_items.facts_attempted_at`
  queryable). **No pass of the enrichment has yet run under code that knows about
  these fields.**
- **The corroborating null.** `facts_attempted_at` is stamped on every *visited*
  item, found-something or not, and that stamp is itself Phase A's
  (`facts/gather.py:181-185`, added by the same `253519e`). It is NULL on all
  15821 media items — **0** non-NULL. If any sweep had visited any item under the
  deployed code, this would be non-zero. It is not.
- **The TMDb writer is not broken.** **12458 of 13590** fact rows (91.7%) carry
  at least one field whose recorded provider is `tmdb` — `audience_rating` on
  12452 rows, `genres` and `originally_available` on 2252, `studio` on 2236. The
  TMDb credential resolves, `tmdb_id`s resolve, and the gather does not
  short-circuit. What is missing is precisely and only the three fields that did
  not exist in the code when those rows were last written; the zero counts for
  the `tmdb_origin_country` / `tmdb_original_language` / `tmdb_collection_id`
  source keys are the *never-written* signature, not a *written-empty* one. This
  is consistent with `facts/gather.py:207-210`, which writes each of those
  columns only when the gather populated it, and with `db/models.py:233`, which
  makes `tmdb_origin_country` NOT NULL defaulting to `[]` — hence 13590 non-NULL
  rows holding 13590 empty arrays.

So hypothesis (b) — "the sweep runs but the writer never populates these fields"
— is **excluded by measurement**, not set aside by argument.

**How long the fill takes.** The refill is the ratings-drift sweep
(`scheduler/jobs.py:211,216`), and its pace decides how long the columns stay
empty. It ticks every `drift_days = 7` (`config/schema.py:1078`)
and enqueues at most `drift_batch_size = 500` items per tick
(`config/schema.py:1082`) whose facts are older than `drift_max_age_days = 7`
(`config/schema.py:1083`).

The candidate pool is **not** the 13590 fact rows, and getting that denominator
right is what makes this number safe to repeat to an operator. The sweep selects
`MediaItem.kind IN ('movie', 'show')` (`scheduler/jobs.py:161`); seasons and
episodes are deliberately excluded and ride their parent's pass
(`jobs.py:131-132`). So the population is **1961 movies + 291 shows = 2252
items** — corroborated by this document's own capture, where `SRC genres tmdb
2252` is exactly that parent count. Every one of the 13590 fact rows was written
in the 2026-08-23 21:50Z – 2026-08-28 14:00Z backfill window, so rows only begin
ageing past the 7-day threshold from ~2026-08-30 onward; at 500 candidates per
weekly tick, a full revisit is `ceil(2252 / 500)` = **5 ticks ≈ 5 weeks** at the
default knobs. The 1961 movie rows the two location packs serve sit inside that
population, so pack membership substantially fills within about a month — not
the half-year a 13590-row denominator would predict.

**What this means for the packs.** The Addendum's binding disclosure — that
membership *converges as the drift sweep fills `origin_country`* — is honest: the
mechanism exists, is deployed, and has simply not ticked yet. The convergence is
slow, not absent. Anyone wanting it sooner turns the two knobs above rather than
waiting.

### The re-check, 2026-08-30 08:56Z: verdict unchanged, and now for a stated reason

The verdict above is production state at one instant, and the review that
accepted it said so: all 13590 rows were written 08-23…08-28 against a 7-day
threshold, so the first *eligible* tick fell about then, and "a tick that ran
while the columns stayed empty" would revive the writer-bug hypothesis and
withdraw the disclosure. The wrap therefore re-ran the same aggregate — same
query shape, same read-only `kubectl exec` — 1h57m later
(`.superpowers/sdd/p-locnames-db3.txt`):

```
AGG	now()	2026-08-30 08:56:36.118188+00:00
AGG	max(fetched_at) overall	2026-08-28 14:00:53.302242+00:00
AGG	max(updated_at) overall	2026-08-28 14:00:53.302242+00:00
AGG	max(facts_attempted_at) media_items	None
AGG	media_items with facts_attempted_at NOT NULL	0
AGG	item_facts rows fetched since 2026-08-29	0
AGG	item_facts rows fetched since 2026-08-30	0
COL	origin_country NOT NULL	13590
COL	origin_country non-empty array	0
COL	original_language NOT NULL	0
COL	collection_id NOT NULL	0
COL	movie-kind origin_country non-empty	0
SRC	audience_rating	tmdb	12452
SRC	genres	tmdb	2252
```

Every number in the substantive rows is byte-identical to the 06:59Z capture. **No tick has run**, so
the third case — a tick that ran and left the columns empty — did not arise and
the writer-bug hypothesis stays excluded. The `SRC` breakdown is unchanged too:
the TMDb writer is still demonstrably working on the six fields it knows.

The re-check also replaced "about now" with a computed moment. The sweep's
predicate is `fetched_at < now() - drift_max_age_days` over
`kind IN ('movie','show')` (`scheduler/jobs.py:154,161-162`), so eligibility
begins at `min(fetched_at) + 7 days` at the shipped default
`drift_max_age_days: 7` — a deployment that overrides it moves this moment.
Asked directly
(`.superpowers/sdd/p-locnames-db3.txt`, second block):

```
ELIG	now()	2026-08-30 08:57:13.757911+00:00
ELIG	sweep candidate pool (movie+show)	2252
ELIG	ELIGIBLE right now	0
ELIG	movie/show with NO facts row	0
ELIG	first eligibility moment (min fetched_at + 7d)	2026-08-30 21:50:16.919919+00:00
```

Three things that capture settles which the first one only implied:

- **Zero of the 2252 candidates are eligible yet.** The columns are not empty
  because a sweep looked and skipped them; they are empty because the sweep has
  nothing it is *allowed* to pick up. The first item ages past the threshold at
  **2026-08-30 21:50:16Z**, about 13 hours after this re-check — so the earliest
  a tick can enqueue anything is that evening, and the 5-tick fill starts from
  there.
- **The 2252 candidate pool is now counted, not inferred.** The estimate above
  derives it from the `SRC genres tmdb 2252` coincidence; this query counts
  `media_items` by kind directly and returns the same 2252, which is the number
  the ≈5-tick figure divides.
- **No movie or show is missing a facts row** (0), so the pool and the fact-row
  population are the same set — an item with no facts row would have been
  eligible immediately (`fetched_at IS NULL`), and none exists.

None of this changes a shipped number or a disclosure. It removes the
time-bound: the convergence story is the same story it was at 06:59Z, and the
reason the columns are empty is now a scheduled moment rather than an absence of
evidence to the contrary.

---

## 3. The join, per name

`.superpowers/sdd/p-locnames-join.txt`, verbatim:

```
D5 duplicate english_name entries: {'Congo': ['CD', 'CG']}
member universes: region=346 continent=330

== library join, per name (movie kind is what the packs serve) ==
kind	code	items	tmdb_name	region	continent

== forward join, full fetched table ==
of 251 fetched names: 10 not in region universe, 10 not in continent
region misses: ['Cocos  Islands', "Cote D'Ivoire", 'Faeroe Islands', 'Guadaloupe', 'Kyrgyz Republic', 'Libyan Arab Jamahiriya', 'Palestinian Territory', 'Pitcairn Island', 'Reunion', 'Svalbard & Jan Mayen Islands']
continent misses: ['Cocos  Islands', "Cote D'Ivoire", 'Faeroe Islands', 'Guadaloupe', 'Kyrgyz Republic', 'Libyan Arab Jamahiriya', 'Palestinian Territory', 'Pitcairn Island', 'Reunion', 'Svalbard & Jan Mayen Islands']

== languages (D6) ==
fetched language entries with empty english_name: none
library language codes with no usable name (title falls back to the code, by design): none
```

The per-name library table is empty for the reason section 2 gives, and the two
`D3/D4` percentage lines the script emits per media kind are therefore absent
from the output — there were no kinds to emit them for. That absence is the
measurement, not a gap in it.

The **forward join** over the whole fetched table stands on its own and is what
remains measurable: **241 of 251** TMDb country names (96.0%) land in
`region.yml`'s member universe, and the same **241 of 251** (96.0%) land in
`continent.yml`'s — the two miss lists are identical, name for name.

### Supplementary: what the ten misses are

Ten misses out of 251 is only useful if it is known whether they are countries
upstream does not group at all, or the same countries under a different
spelling. Measured, offline, against the same bytes
(`.superpowers/sdd/p-locnames-miss-detail.txt`, verbatim):

```
supplementary: are the 10 forward-join misses absent countries, or other spellings?
code	tmdb_name	accent/punct-folded region hit	prefix-3 candidates in region
CC	'Cocos  Islands'	NONE	['Cocos (Keeling) Islands']
CI	"Cote D'Ivoire"	Côte d’Ivoire	["Côte d'Ivoire", 'Côte d’Ivoire']
FO	'Faeroe Islands'	NONE	NONE
GP	'Guadaloupe'	NONE	['Guadeloupe', 'Guam', 'Guatemala']
KG	'Kyrgyz Republic'	NONE	['Kyrgyzstan']
LY	'Libyan Arab Jamahiriya'	NONE	['Liberia', 'Libya']
PN	'Pitcairn Island'	NONE	['Pitcairn', 'Pitcairn Islands']
PS	'Palestinian Territory'	NONE	['Palau', 'Palestine']
RE	'Reunion'	Réunion	['Réunion']
SJ	'Svalbard & Jan Mayen Islands'	NONE	['Svalbard and Jan Mayen', 'Svalbard and Jan Mayen Islands']

folded-join totals: region 243/251 matched, continent 243/251 matched
```

Two of the ten — `CI` and `RE` — are pure accent differences: an
accent/punctuation fold recovers both (third column), taking the forward join
from 241/251 to **243/251** (96.8%). The other eight differ by more than
accents and no fold reaches them.

#### The one row the table above leaves undecided: `FO`

Nine of the ten rows carry their own evidence in the capture — a folded hit, a
prefix-3 candidate, or both. `FO 'Faeroe Islands'` shows `NONE NONE`, because the
prefix-3 heuristic cannot bridge `fae` → `far` and the accent fold has no accent
to strip. The pairing below is therefore derived from the on-disk bytes
explicitly, so that Task 2's alias table has a derivation to cite and nobody has
to supply `Faroe Islands` from memory — which would be right, and would still be
the forbidden move.

**Both spellings are fetched bytes on disk**, in two independent artifacts:

| spelling | artifact | where |
| --- | --- | --- |
| `Faeroe Islands` | `.superpowers/sdd/p-locnames-countries.json` (TMDb `/configuration/countries`, sha256 `fb4609a1…`) | the `FO` record's `english_name`; the record object begins at byte offset 5398 (the `"FO"` token itself is at 5412) |
| `Faroe Islands` | *the same TMDb record*, `native_name` | the same object at 5398 |
| `Faroe Islands` | `.superpowers/kometa-v2.4.8/region.yml` (sha256 `9409fee7…`) | line 328, an addon member of group `Northern Europe` (key at line 321) |
| `Faroe Islands` | `.superpowers/kometa-v2.4.8/continent.yml` (sha256 `fbc20666…`) | line 315, an addon member of group `Europe` (key at line 291) |

The whole `FO` record, from the fetched bytes:

```json
{"iso_3166_1": "FO", "english_name": "Faeroe Islands", "native_name": "Faroe Islands"}
```

So the pair `Faeroe Islands` → `Faroe Islands` is derivable **without leaving the
fetched artifacts**: TMDb's own record carries the upstream spelling in its
`native_name` field, and upstream's two files carry that exact string as a
groupable member. Counter-checks over the same bytes, both directions:
`Faeroe` occurs **0** times in `region.yml` and **0** times in `continent.yml`;
`Faroe` occurs exactly **1** time in the fetched countries table — the
`native_name` above. There is no third spelling to choose between.

`native_name` is a general second lever, not an `FO` special case: 217 of the 251
fetched country rows have `native_name == english_name`, leaving 34 rows where it
supplies a genuinely different string. It is offered here as evidence for one
pairing, not proposed as a normalisation rule — see the note that closes this
section.

Every one of the ten is a **spelling divergence for a country upstream does
carry**: `Faeroe Islands`/`Faroe Islands`, `Kyrgyz Republic`/`Kyrgyzstan`,
`Libyan Arab Jamahiriya`/`Libya`, `Palestinian Territory`/`Palestine`,
`Guadaloupe`/`Guadeloupe`, `Pitcairn Island`/`Pitcairn`,
`Cocos  Islands`/`Cocos (Keeling) Islands`, `Svalbard & Jan Mayen
Islands`/`Svalbard and Jan Mayen Islands`. **Not one of the 251 TMDb countries
is missing from upstream's tables as a place.** The residual gap is
name-normalisation, not coverage — which is exactly the Türkiye/Turkey class
the facts file (C2) anticipated, with the twist that here upstream's alias set
does not happen to carry TMDb's spelling.

No normalisation beyond exact string equality is proposed by this document.
The fold above is a measurement, not a design: whether Task 2/3 should fold
accents, or hard-code the eight remaining spelling bridges as NOT_KOMETA
additions, or let all ten fall into `Other Regions`/`Other Continents` and
disclose them, is a decision for the plan's owner and not one T1 makes.

### What Task 2 decided: `COUNTRY_NAME_ALIASES`, and the rule that derived it

T1 left the choice open; T2 took the second option and carries all **ten**
bridges as an OURS-marked table in `src/autoposter/collections/iso_names.py`
(`COUNTRY_NAME_ALIASES`, digest-guarded in `tests/test_collection_iso_names.py`).
Ten rather than the eight the phase Addendum names, because the eight presumes
the accent fold measured above and **no accent fold exists anywhere at
runtime** — `derive_keys` compares raw strings, the family layer does a plain
dict lookup, and `iso_names` folds nothing. Carrying `CI` and `RE` here too
means no caller has to fold; the eight are a subset of the ten.

The generator that emitted the table was deleted after its run, as the plan
requires. **The rule it applied is recorded here** so the table stays
reconstructible from committed artifacts alone. Three layers, tried in order,
each requiring the **same** answer in `region.yml` **and** `continent.yml`, over
a country whose `english_name` is a member of neither:

| layer | rule | pairs |
| --- | --- | --- |
| A | TMDb's own `native_name` for that code is an exact member of both universes | 8 — `CC`, `CI`, `FO`, `GP`, `KG`, `LY`, `PN`, `RE` |
| B | an accent/punctuation/**ampersand** fold of the `english_name` matches exactly one member of each universe | 1 — `SJ` |
| C | exactly one member of each universe shares a **six**-character folded prefix with the `english_name` | 1 — `PS` |

The fold used by layer B (and by C's prefix) is: NFKD-normalise, read `&` as
`and`, drop combining marks, keep lowercased alphanumerics only.

There is no layer D. A divergence no layer reaches **halts the generation** —
`assert target, "no derivation for %s %r -- refusing to invent one"` — rather
than being typed in from memory. Layer A is this section's own `FO` derivation
generalised, and it answers 8 of the 10 on its own, so the two weaker rules
carry one pair each.

The ten pairs, with the line the upstream member sits on in each file (the
generator printed these; `FO -> region.yml:328, continent.yml:315` is the pair
derived by hand above, reproduced by the rule):

```
CC 'Cocos  Islands'               -> 'Cocos (Keeling) Islands'        via A native_name  region.yml:379 continent.yml:367
CI "Cote D'Ivoire"                -> 'Côte d’Ivoire'                  via A native_name  region.yml:131 continent.yml:115
FO 'Faeroe Islands'               -> 'Faroe Islands'                  via A native_name  region.yml:328 continent.yml:315
GP 'Guadaloupe'                   -> 'Guadeloupe'                     via A native_name  region.yml:170 continent.yml:155
KG 'Kyrgyz Republic'              -> 'Kyrgyzstan'                     via A native_name  region.yml:234 continent.yml:220
LY 'Libyan Arab Jamahiriya'       -> 'Libya'                          via A native_name  region.yml:76  continent.yml:60
PN 'Pitcairn Island'              -> 'Pitcairn Islands'               via A native_name  region.yml:411 continent.yml:400
PS 'Palestinian Territory'        -> 'Palestine'                      via C prefix6      region.yml:297 continent.yml:283
RE 'Reunion'                      -> 'Réunion'                        via A native_name  region.yml:95  continent.yml:79
SJ 'Svalbard & Jan Mayen Islands' -> 'Svalbard and Jan Mayen Islands' via B fold         region.yml:337 continent.yml:324
```

Two of those rows resolve a candidate list this section left plural, and both
resolve **by rule** rather than by taste:

- **`PN`** takes `Pitcairn Islands`, not the `Pitcairn` this section's
  divergence list names — layer A takes TMDb's `native_name`, which is the
  longer spelling. Both are members of the **same** group in both files
  (`Polynesia` / `Oceania`), so grouping is identical either way; only the
  displayed string differs. Noted so the next reader diffing this document
  against the module does not have to re-derive it.
- **`SJ`** takes `Svalbard and Jan Mayen Islands` over the also-present
  `Svalbard and Jan Mayen` because the fold of TMDb's `english_name` carries
  "Islands" and so matches that member exactly. Both spellings sit in one group
  in both files (`Northern Europe` / `Europe`), so this too is display-only.

**Layer C's free parameter, and why six.** A prefix heuristic is only honest if
its length was not tuned until it produced an answer, so the one pair it
decides was measured across lengths 3–10 against both universes:

| prefix length | members whose folded form shares it with `palestinianterritory` |
| --- | --- |
| 3 (`pal`) | `Palau`, `Palestine` — **ambiguous**, which is why this section's prefix-3 column shows two candidates |
| 4–8 | `Palestine` — **unique at every length in this range** |
| 9+ | none — `palestinianterritory` and `palestine` diverge at the 9th character |

Six sits in the middle of a five-wide plateau, not on a knife edge, and it is
the *more* conservative parameter than the prefix-3 this section measured, not
the looser one. Layer A genuinely fails for `PS` before C is reached: TMDb's
`native_name` there is `Palestinian Territories` (plural), a member of neither
universe. `Palestine` is a real member of a real group in both files
(`Western Asia` / `Asia`), so dropping the pair would send `PS`-origin items to
`Other Regions` / `Other Continents` for a country upstream demonstrably does
carry.

**The table is data, not behaviour.** `COUNTRY_NAMES` stays TMDb's bytes
verbatim so `country_codes` can still fold a family key back to the stored code;
nothing in `iso_names` applies an alias. **Applying** them is the packs' job
(Task 3), as an OURS/NOT_KOMETA-marked overlay beside the verbatim
transcription.

---

## 4. §Decisions

| row | decision | evidence |
| --- | --- | --- |
| D1 | countries fetch **OK** | HTTP 200, 251 entries, 18835 bytes, sha256 `fb4609a1…`; shape assertion passed on all 251 rows |
| D2 | languages fetch **OK** | HTTP 200, 187 entries, 11133 bytes, sha256 `6e23ef1a…`; shape assertion passed on all 187 rows |
| D3 | region join: movie-kind item-values matched **n/a (0 of 0)** — **BLOCKED-FOR-ADJUDICATION** | the library holds zero origin-coded item-values (§2: `origin_country non-empty array 0` over 13590 fact rows), so the plan's `≥90% of movie-kind origin-coded item-values` is undefined rather than met or missed. Substitute evidence, all that is measurable today: forward join 241/251 exact (96.0%), 243/251 accent-folded (96.8%), ten misses named in §3, all ten spelling variants of countries upstream carries |
| D4 | continent join: movie-kind item-values matched **n/a (0 of 0)** — **BLOCKED-FOR-ADJUDICATION** | identical to D3, name for name: same ten misses, same 241/251 and 243/251, against `continent.yml`'s 330-member universe |
| D5 | duplicate `english_name`: **one, listed** | `{'Congo': ['CD', 'CG']}` — the name→code fold is one-to-many for `Congo` and must expand to both codes; every other one of the 251 names is unique to its code |
| D6 | language names: **every fetched code named; the library's half is vacuous** | zero fetched entries have an empty `english_name` (all 187 usable); the library stores no `tmdb_original_language` at all (§2), so no stored code needs the code-fallback today. The fallback stays required by design for codes TMDb does not name later |

### What D3/D4 mean for Task 3

The plan's PROCEED threshold (Global Constraint 11, plan Step 6) is *≥90% of
movie-kind origin-coded item-values land in a named group for that pack*. It
cannot be evaluated: the library has no such item-values. Per the plan's own
rule — "a pack whose join misses too much re-files with the measured numbers —
honesty over momentum" — this is not a PROCEED, and T1 does not manufacture one
from the forward join. **Both packs' halves of Task 3 are
BLOCKED-FOR-ADJUDICATION.** The controller's call, on evidence that is now
complete on the fetched side:

- The **name-table join is healthy** where it can be measured: 96.0% exact,
  96.8% accent-folded, zero countries absent as places, one duplicate name
  (`Congo`) that the fold-back already handles.
- The **library-side gate is unmeasurable**, and will stay unmeasurable until
  the TMDb enrichment populates `tmdb_origin_country` in production. Whichever
  way that gate is re-scoped, the packs would serve empty collections today,
  because there is no stored origin data for the query seam to match on.

Re-scoping the gate to the forward join, deferring the flips until the
enrichment has run, or flipping with the leftover-bucket disclosure the plan
describes are all defensible readings — and all three are the controller's to
choose, not this document's.

### The operative gate (superseding the plan's original wording)

That choice has since been made, and it is recorded here so a reader of this
document alone meets the gate that is actually in force rather than the one it
replaced.

**Superseded.** The plan as committed
(`docs/superpowers/plans/2026-08-31-location-names.md:1037`, and Global
Constraint 11) words the Task 3 gate as *D3, D4 = PROCEED (≥90% of movie-kind
origin-coded item-values land in a named group)*, admitting only PROCEED or
RE-FILE. That threshold is **unmeasurable, permanently as of this measurement
and not merely unmet**: its denominator is the count of origin-coded movie-kind
item-values in production, which is **0 of 0** (§2). Neither branch of a
two-branch gate can be taken when the quantity it tests does not exist.

**Operative.** The controller re-scoped the gate to the **forward join** —
`p-locnames-facts.md`, Addendum of 2026-08-31, item 1, binding — on the reasoning
that the forward join answers the gate's actual semantic question (*do TMDb's
country names land in upstream's groups?*) using the half of the evidence that is
measurable today. The re-scoped gate and the numbers that satisfy it:

| | |
| --- | --- |
| **gate** | ≥90% of the **fetched TMDb country names** land in the pack file's member universe |
| **region.yml** | **241/251 = 96.0%** exact; 243/251 = 96.8% accent-folded |
| **continent.yml** | **241/251 = 96.0%** exact; 243/251 = 96.8% accent-folded — the same ten misses, name for name |
| **misses** | all ten are spelling divergences for countries upstream does carry (§3); **not one of the 251 is absent as a place** |
| **verdict** | **D3 = PROCEED, D4 = PROCEED.** Task 3 flips both packs. |

Two conditions ride with that PROCEED (Addendum item 2), and neither is
optional:

1. The flip ships the **convergence disclosure**: membership grows from near-zero
   as the drift sweep fills `tmdb_origin_country`, with
   `scheduler.drift_batch_size` / `scheduler.drift_days` named as the knobs that
   accelerate it. §2 above establishes by measurement that this sentence is true
   — the sweep is deployed and has not yet ticked, rather than running and
   failing to write — which is the condition on which the disclosure may honestly
   ship. §2's re-check at the wrap (08:56Z) re-tested that condition rather than
   inheriting it, and tightened it: **0 of 2252** candidates are eligible until
   `2026-08-30 21:50:16Z`, so the sweep has not merely not ticked, it has had
   nothing it may pick up.
2. The **eight post-fold spelling divergences** get an OURS-marked alias table in
   Task 2's module, every pair derived by comparing the two fetched artifacts on
   disk rather than recalled, each carrying its own digest guard. §3's `FO`
   subsection supplies the one derivation the capture did not already carry.

Anything still unmatched after the alias table falls to the packs' leftover
handling (`Other Regions` / `Other Continents`), disclosed.

---

## 5. The probe scripts, verbatim

Reproduced so the fetch and the measurement are repeatable after the transient
scripts were deleted. The tables above were produced by exactly these.

### `.superpowers/sdd/p-locnames-fetch.py` — the two configuration fetches

```python
"""Fetch TMDb's two /configuration name tables, once, saving raw bytes.

Runs inside the plocn1 test container (deps installed, tree mounted at /app).
The read token comes from /app/.env's AUTOPOSTER_TMDB_TOKEN -- the facts file
(C1) names the .env TMDb token for this one fetch. It is never printed: the
URLs carry no token (Bearer header), and nothing below prints headers.
"""
import hashlib
import json
import sys

import httpx


def env_token(path="/app/.env"):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("AUTOPOSTER_TMDB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("AUTOPOSTER_TMDB_TOKEN not found in /app/.env -- fill .env in "
             "from .env.example before running this probe")


token = env_token()
for name, path in (("countries", "/configuration/countries"),
                   ("languages", "/configuration/languages")):
    resp = httpx.get(
        "https://api.themoviedb.org/3" + path,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    raw = resp.content
    dest = "/app/.superpowers/sdd/p-locnames-%s.json" % name
    with open(dest, "wb") as fh:
        fh.write(raw)
    rows = json.loads(raw)
    print("%s: HTTP %d, %d bytes, %d entries, sha256 %s" % (
        name, resp.status_code, len(raw), len(rows),
        hashlib.sha256(raw).hexdigest()))
```

Run as (no `--rm`; the log is read from the host afterwards, the container is
removed by name, the project is torn down without `-v`):

```bash
docker compose -p plocn1 -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name plocn1-fetch test sh -c \
  "set -o pipefail; python /app/.superpowers/sdd/p-locnames-fetch.py 2>&1 | tee /app/.superpowers/run-plocn-fetch.log"
docker wait plocn1-fetch
docker rm plocn1-fetch
docker compose -p plocn1 down
```

### `.superpowers/sdd/p-locnames-db.py` — the read-only library probe

```python
"""Distinct stored origin_country / original_language codes, with counts.

Read-only. Run as:
  kubectl exec -n media -i deploy/autoposter -- python - < p-locnames-db.py
The connection URL comes from the pod's own environment and is never printed.
Output is TSV: ORIGIN/LANG, media kind, code, item count.
"""
import asyncio
import os

import asyncpg


async def main():
    url = os.environ["AUTOPOSTER_DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT mi.kind, value AS code, count(*) AS n "
            "FROM item_facts f "
            "JOIN media_items mi ON mi.id = f.item_id "
            "CROSS JOIN LATERAL jsonb_array_elements_text(f.tmdb_origin_country) AS value "
            "GROUP BY mi.kind, value ORDER BY mi.kind, n DESC, value"
        )
        for row in rows:
            print("ORIGIN\t%s\t%s\t%d" % (row["kind"], row["code"], row["n"]))
        rows = await conn.fetch(
            "SELECT mi.kind, f.tmdb_original_language AS code, count(*) AS n "
            "FROM item_facts f "
            "JOIN media_items mi ON mi.id = f.item_id "
            "WHERE f.tmdb_original_language IS NOT NULL "
            "GROUP BY mi.kind, f.tmdb_original_language "
            "ORDER BY mi.kind, n DESC, code"
        )
        for row in rows:
            print("LANG\t%s\t%s\t%d" % (row["kind"], row["code"], row["n"]))
    finally:
        await conn.close()


asyncio.run(main())
```

### The diagnostic block — appended to that probe for one run, then removed

The probe above returned nothing. This block was appended to it (and
`asyncio.run(diagnose())` called after `asyncio.run(main())`) for a single run
to establish whether the emptiness was the answer or a plumbing failure, then
deleted so the probe stayed verbatim. It is read-only, like the probe.

```python
async def diagnose():
    url = os.environ["AUTOPOSTER_DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(url)
    try:
        for label, sql in (
            ("media_items rows", "SELECT count(*) FROM media_items"),
            ("item_facts rows", "SELECT count(*) FROM item_facts"),
            ("item_facts joined to media_items",
             "SELECT count(*) FROM item_facts f JOIN media_items mi ON mi.id = f.item_id"),
            ("origin_country NOT NULL",
             "SELECT count(*) FROM item_facts WHERE tmdb_origin_country IS NOT NULL"),
            ("origin_country non-empty array",
             "SELECT count(*) FROM item_facts WHERE jsonb_array_length("
             "coalesce(tmdb_origin_country, '[]'::jsonb)) > 0"),
            ("original_language NOT NULL",
             "SELECT count(*) FROM item_facts WHERE tmdb_original_language IS NOT NULL"),
        ):
            print("DIAG\t%s\t%s" % (label, await conn.fetchval(sql)))
        for row in await conn.fetch(
            "SELECT kind, count(*) AS n FROM media_items GROUP BY kind ORDER BY n DESC"
        ):
            print("DIAGKIND\t%s\t%d" % (row["kind"], row["n"]))
        for row in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'item_facts' ORDER BY ordinal_position"
        ):
            print("DIAGCOL\t%s" % row["column_name"])
    finally:
        await conn.close()
```

### `.superpowers/sdd/p-locnames-db2.py` — the sweep-timing aggregate

Added after review, to settle by measurement whether §2's empty columns are a
sweep that has not ticked or a writer that does not write. Read-only, like the
probe above; deleted after the run. Run as:

```bash
kubectl exec -n media -i deploy/autoposter -- python - < .superpowers/sdd/p-locnames-db2.py \
  2>&1 | tee .superpowers/sdd/p-locnames-db2.txt
```

```python
"""Read-only aggregate: has the TMDb facts writer run against production since
Phase A, or has the drift sweep simply not revisited these rows yet?

Run as:
  kubectl exec -n media -i deploy/autoposter -- python - < p-locnames-db2.py
The connection URL comes from the pod's own environment and is never printed.

NOTE on the query shape: item_facts.sources is a JSONB OBJECT mapping FIELD
NAME -> PROVIDER (db/models.py:243, facts/gather.py:80, tmdb_facts.py:119-129),
so `sources ? 'tmdb'` would test KEYS and always return zero. The provider is
the VALUE, hence jsonb_each_text below.
"""
import asyncio
import os

import asyncpg

TMDB_ROW = (
    "EXISTS (SELECT 1 FROM jsonb_each_text(f.sources) s WHERE s.value = 'tmdb')"
)


async def main():
    url = os.environ["AUTOPOSTER_DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(url)
    try:
        for label, sql in (
            ("now()", "SELECT now()"),
            ("item_facts rows", "SELECT count(*) FROM item_facts f"),
            ("rows with ANY tmdb-sourced field",
             "SELECT count(*) FROM item_facts f WHERE " + TMDB_ROW),
            ("rows with sources key tmdb_origin_country",
             "SELECT count(*) FROM item_facts f "
             "WHERE f.sources ? 'tmdb_origin_country'"),
            ("rows with sources key tmdb_original_language",
             "SELECT count(*) FROM item_facts f "
             "WHERE f.sources ? 'tmdb_original_language'"),
            ("rows with sources key tmdb_collection_id",
             "SELECT count(*) FROM item_facts f "
             "WHERE f.sources ? 'tmdb_collection_id'"),
            ("rows with non-empty sources",
             "SELECT count(*) FROM item_facts f "
             "WHERE f.sources IS NOT NULL AND f.sources <> '{}'::jsonb"),
            ("max(fetched_at) overall", "SELECT max(f.fetched_at) FROM item_facts f"),
            ("max(fetched_at) tmdb-sourced rows",
             "SELECT max(f.fetched_at) FROM item_facts f WHERE " + TMDB_ROW),
            ("max(updated_at) overall", "SELECT max(f.updated_at) FROM item_facts f"),
            ("min(fetched_at) overall", "SELECT min(f.fetched_at) FROM item_facts f"),
            ("max(facts_attempted_at) media_items",
             "SELECT max(facts_attempted_at) FROM media_items"),
            ("media_items with facts_attempted_at NOT NULL",
             "SELECT count(*) FROM media_items WHERE facts_attempted_at IS NOT NULL"),
            ("movie-kind facts rows",
             "SELECT count(*) FROM item_facts f JOIN media_items mi "
             "ON mi.id = f.item_id WHERE mi.kind = 'movie'"),
            ("max(fetched_at) movie-kind facts rows",
             "SELECT max(f.fetched_at) FROM item_facts f JOIN media_items mi "
             "ON mi.id = f.item_id WHERE mi.kind = 'movie'"),
            ("item_facts rows fetched in last 7 days",
             "SELECT count(*) FROM item_facts f "
             "WHERE f.fetched_at > now() - interval '7 days'"),
            ("item_facts rows fetched since 2026-08-29",
             "SELECT count(*) FROM item_facts f "
             "WHERE f.fetched_at >= timestamptz '2026-08-29 00:00+00'"),
        ):
            print("AGG\t%s\t%s" % (label, await conn.fetchval(sql)))
        for row in await conn.fetch(
            "SELECT s.key AS k, s.value AS v, count(*) AS n "
            "FROM item_facts f, jsonb_each_text(f.sources) s "
            "GROUP BY s.key, s.value ORDER BY n DESC, s.key"
        ):
            print("SRC\t%s\t%s\t%d" % (row["k"], row["v"], row["n"]))
    finally:
        await conn.close()
    print("SENTINEL-SCRIPT-REACHED-END")


asyncio.run(main())
```

### `.superpowers/sdd/p-locnames-db3.py` — the T4 re-check

The wrap's re-run of the aggregate above, settling the review's time-bound.
Read-only; deleted after the run; capture at
`.superpowers/sdd/p-locnames-db3.txt`. Run the same way:

```bash
kubectl exec -n media -i deploy/autoposter -- python - < .superpowers/sdd/p-locnames-db3.py \
  2>&1 | tee .superpowers/sdd/p-locnames-db3.txt
```

Its body is `p-locnames-db2.py` verbatim, plus one extra `AGG` row
(`item_facts rows fetched since 2026-08-30`, the same shape as the 08-29 one)
and a `COL` block carrying the first diagnostic's column-population counts, so
that "has a tick run?" and "are the columns filling?" are answered in one
capture rather than two:

```python
        for label, sql in (
            ("origin_country NOT NULL",
             "SELECT count(*) FROM item_facts WHERE tmdb_origin_country IS NOT NULL"),
            ("origin_country non-empty array",
             "SELECT count(*) FROM item_facts WHERE jsonb_array_length("
             "coalesce(tmdb_origin_country, '[]'::jsonb)) > 0"),
            ("original_language NOT NULL",
             "SELECT count(*) FROM item_facts WHERE tmdb_original_language IS NOT NULL"),
            ("collection_id NOT NULL",
             "SELECT count(*) FROM item_facts WHERE tmdb_collection_id IS NOT NULL"),
            ("movie-kind origin_country non-empty",
             "SELECT count(*) FROM item_facts f JOIN media_items mi "
             "ON mi.id = f.item_id WHERE mi.kind = 'movie' AND "
             "jsonb_array_length(coalesce(f.tmdb_origin_country, '[]'::jsonb)) > 0"),
        ):
            print("COL\t%s\t%s" % (label, await conn.fetchval(sql)))
```

### `.superpowers/sdd/p-locnames-db4.py` — the eligibility count

Mirrors `sweep_stale_facts`' own predicate (`scheduler/jobs.py:154,158-165`) so
"how many items can the next tick actually pick up?" is measured against the
sweep's real `WHERE`, not a paraphrase of it. Same connection handling; the
capture is appended to `p-locnames-db3.txt`.

```python
ELIGIBLE = (
    "FROM media_items mi LEFT JOIN item_facts f ON f.item_id = mi.id "
    "WHERE mi.kind IN ('movie','show') AND "
    "(f.fetched_at IS NULL OR f.fetched_at < now() - interval '7 days')"
)

        for label, sql in (
            ("now()", "SELECT now()"),
            ("sweep candidate pool (movie+show)",
             "SELECT count(*) FROM media_items WHERE kind IN ('movie','show')"),
            ("ELIGIBLE right now", "SELECT count(*) " + ELIGIBLE),
            ("movie/show with NO facts row",
             "SELECT count(*) FROM media_items mi LEFT JOIN item_facts f "
             "ON f.item_id = mi.id WHERE mi.kind IN ('movie','show') "
             "AND f.item_id IS NULL"),
            ("first eligibility moment (min fetched_at + 7d)",
             "SELECT min(f.fetched_at) + interval '7 days' FROM item_facts f "
             "JOIN media_items mi ON mi.id = f.item_id "
             "WHERE mi.kind IN ('movie','show')"),
        ):
            print("ELIG\t%s\t%s" % (label, await conn.fetchval(sql)))
```

### The `FO` byte-evidence commands

The §3 `FO` subsection's four locations, reproduced so the pairing is
re-derivable. Offline, over the already-fetched bytes:

```bash
grep -Fn 'Faroe' .superpowers/kometa-v2.4.8/region.yml .superpowers/kometa-v2.4.8/continent.yml
grep -Fc 'Faeroe' .superpowers/kometa-v2.4.8/region.yml .superpowers/kometa-v2.4.8/continent.yml
grep -Fc 'Faroe' .superpowers/sdd/p-locnames-countries.json
```

```
.superpowers/kometa-v2.4.8/region.yml:328:        - Faroe Islands
.superpowers/kometa-v2.4.8/continent.yml:315:        - Faroe Islands
.superpowers/kometa-v2.4.8/region.yml:0
.superpowers/kometa-v2.4.8/continent.yml:0
.superpowers/sdd/p-locnames-countries.json:1
```

The enclosing addon groups were resolved by the same `universe()` fold section 3
uses (`region.yml` → `Northern Europe`, `continent.yml` → `Europe`; an addon
member in both, in neither `include:` list), and the `FO` record was read
straight out of the fetched JSON.

### `.superpowers/sdd/p-locnames-join.py` — the join measurement

```python
"""The join measurement facts C2/C5 gate on. Offline: fetched bytes only.

For every TMDb country name reachable from the library's stored origin codes:
does it land in region.yml's member universe? continent.yml's? Misses are
recorded PER NAME, never assumed away. Also measured: the full-table forward
join (all fetched codes, for libraries this one has not become yet), the
duplicate-english_name check (the fold-back's soundness), and whether every
stored language code has a non-empty english_name.
"""
import json
from pathlib import Path

import yaml

SDD = Path(".superpowers/sdd")
UP = Path(".superpowers/kometa-v2.4.8")

countries = json.loads((SDD / "p-locnames-countries.json").read_bytes())
languages = json.loads((SDD / "p-locnames-languages.json").read_bytes())
country_names = {r["iso_3166_1"]: r["english_name"] for r in countries}
language_names = {r["iso_639_1"]: r["english_name"] for r in languages}

# duplicate-name check: two codes sharing one english_name would make the
# name->code fold one-to-many; country_codes() handles that, but it must be
# KNOWN, not assumed absent.
seen = {}
for code, name in country_names.items():
    seen.setdefault(name, []).append(code)
dupes = {n: c for n, c in seen.items() if len(c) > 1}
print("D5 duplicate english_name entries: %s" % (dupes or "none"))


def universe(path):
    doc = yaml.safe_load(path.read_bytes().decode("utf-8"))
    block = next(iter(doc["dynamic_collections"].values()))
    members = set(block["include"])
    for key, mm in block["addons"].items():
        members.add(key)
        members.update(mm)
    return members


region = universe(UP / "region.yml")
continent = universe(UP / "continent.yml")
print("member universes: region=%d continent=%d" % (len(region), len(continent)))

origin = {}  # (kind, code) -> count
for line in (SDD / "p-locnames-db.txt").read_text().splitlines():
    parts = line.split("\t")
    if len(parts) == 4 and parts[0] == "ORIGIN":
        origin[(parts[1], parts[2])] = int(parts[3])

print("\n== library join, per name (movie kind is what the packs serve) ==")
print("kind\tcode\titems\ttmdb_name\tregion\tcontinent")
totals = {}
for (kind, code), n in sorted(origin.items(), key=lambda kv: -kv[1]):
    name = country_names.get(code)
    in_r = name in region if name else False
    in_c = name in continent if name else False
    print("%s\t%s\t%d\t%s\t%s\t%s" % (
        kind, code, n, name or "MISSING-FROM-TABLE",
        "MATCH" if in_r else "MISS", "MATCH" if in_c else "MISS"))
    t = totals.setdefault(kind, [0, 0, 0])  # items, region-matched, continent-matched
    t[0] += n
    t[1] += n if in_r else 0
    t[2] += n if in_c else 0
for kind, (items, r, c) in sorted(totals.items()):
    print("D3/D4 %s: %d origin-coded item-values; region matched %d (%.1f%%), "
          "continent matched %d (%.1f%%)"
          % (kind, items, r, 100.0 * r / items if items else 0.0,
             c, 100.0 * c / items if items else 0.0))

print("\n== forward join, full fetched table ==")
missing_r = sorted(n for n in country_names.values() if n not in region)
missing_c = sorted(n for n in country_names.values() if n not in continent)
print("of %d fetched names: %d not in region universe, %d not in continent"
      % (len(country_names), len(missing_r), len(missing_c)))
print("region misses: %s" % missing_r)
print("continent misses: %s" % missing_c)

print("\n== languages (D6) ==")
lib_langs = set()
for line in (SDD / "p-locnames-db.txt").read_text().splitlines():
    parts = line.split("\t")
    if len(parts) == 4 and parts[0] == "LANG":
        lib_langs.add(parts[2])
empty = sorted(c for c, n in language_names.items() if not n)
unnamed = sorted(c for c in lib_langs if not language_names.get(c))
print("fetched language entries with empty english_name: %s" % (empty or "none"))
print("library language codes with no usable name (title falls back to the "
      "code, by design): %s" % (unnamed or "none"))
```

### The supplementary miss classification

Not part of the plan's three scripts; written to answer whether the ten forward
-join misses are absent countries or other spellings, since that is what the
D3/D4 adjudication turns on. Offline, over the same bytes.

```python
import json, yaml, unicodedata
from pathlib import Path
SDD = Path(".superpowers/sdd"); UP = Path(".superpowers/kometa-v2.4.8")
countries = json.loads((SDD / "p-locnames-countries.json").read_bytes())
def universe(path):
    doc = yaml.safe_load(path.read_bytes().decode("utf-8"))
    block = next(iter(doc["dynamic_collections"].values()))
    m = set(block["include"])
    for key, mm in block["addons"].items():
        m.add(key); m.update(mm)
    return m
region = universe(UP / "region.yml"); continent = universe(UP / "continent.yml")
def fold(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())
region_folded = {fold(m): m for m in region}
continent_folded = {fold(m): m for m in continent}
out = []
misses = [r for r in countries if r["english_name"] not in region]
out.append("supplementary: are the 10 forward-join misses absent countries, or other spellings?")
out.append("code\ttmdb_name\taccent/punct-folded region hit\tprefix-3 candidates in region")
for r in misses:
    code, name = r["iso_3166_1"], r["english_name"]
    f = fold(name)
    hit = region_folded.get(f)
    pref = sorted(m for m in region if fold(m)[:3] == f[:3])
    out.append("%s\t%r\t%s\t%s" % (code, name, hit or "NONE", pref or "NONE"))
out.append("")
out.append("folded-join totals: region %d/%d matched, continent %d/%d matched"
           % (sum(1 for r in countries if fold(r["english_name"]) in region_folded), len(countries),
              sum(1 for r in countries if fold(r["english_name"]) in continent_folded), len(countries)))
Path(".superpowers/sdd/p-locnames-miss-detail.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
```
