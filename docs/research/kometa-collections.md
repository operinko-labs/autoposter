# Kometa Collections — `oscars`, `imdb`, `content_rating_cs` (Phase 3 spec source)

Ground truth for this document, in priority order:

1. **Live production Plex server** (probed directly by the coordinator, dump at
   `scratchpad/plex-collections.txt`, movies only, partial — see gaps below).
2. **Kometa v2.4.8 Python source** (`modules/*.py`, unpacked from the pinned
   image `kometateam/kometa@sha256:c58f6d4af5...`).
3. **Kometa v2.4.8 default collection YAML** (`defaults/award/oscars.yml`,
   `defaults/chart/imdb.yml`, `defaults/both/content_rating_cs.yml`,
   `defaults/templates.yml`), extracted from the same image.
4. **Kometa-Team/Translations `en.yml`** (fetched live from GitHub raw) for
   exact collection name/summary strings.

Where live reality and the defaults YAML disagree, **reality wins** and it is
called out explicitly. All four sources agreed on every point that could be
cross-checked.

**The ownership marker: every Kometa-managed collection carries the Plex
label `Kometa`.** The 18 Common Sense buckets additionally carry
`CommonSense Content Rating`; the 5 Oscars year collections additionally
carry `Oscars Winners Awards`. Anything without the `Kometa` label (269
`<Name> Collection` franchise collections from the Plex/TMDB agent,
Maintainerr's `Deleted Soon`, 5 hand-made collections) must never be touched,
listed, or considered by Phase 3. This is the single fact an implementer
must get right before anything else.

Production enables, per the brief:

- Movies: `default: oscars`, `default: imdb`, `default: content_rating_cs`
- TV Shows: `default: imdb`, `default: content_rating_cs`

---

## 1. Inventory table

| Collection name | Library | Builder | Source | `collection_order` | Live count (movies, probed) |
|---|---|---|---|---|---|
| Oscars Best Picture Winners | Movie | `imdb_award` | IMDb event `ev0000003`, category filter, `winning: true`, `event_year: all` | `custom` (award order) | 23 |
| Oscars Best Director Winners | Movie | `imdb_award` | same event, different category filter | `custom` | 19 |
| Oscars Winners `<year>` (×5, dynamic) | Movie | `imdb_award` (dynamic, type `imdb_awards`) | same event, all categories, `winning: true`, last 5 ceremony years | `custom` | 5 collections, one per year 2022–2026 |
| IMDb Popular | Movie + Show | `imdb_chart` | IMDb chart `popular_movies`/`popular_shows` (GraphQL `MOST_POPULAR_*`, first 100) | `custom` | Movies 37 / Shows 28 |
| IMDb Top 250 | Movie + Show | `imdb_chart` | IMDb chart `top_movies`/`top_shows` (GraphQL `TOP_RATED_*`, first 250) | `custom` | Movies 116 / Shows 38 |
| IMDb Lowest Rated | **Movie only** | `imdb_chart` | IMDb chart `lowest_rated` (GraphQL `LOWEST_RATED_MOVIES`, first 100) | `custom` | 6 |
| Age `N`+ Movies (×18, `N`=1–18) | Movie | dynamic `content_rating` → `smart_filter` | Plex's own distinct `contentRating` values on library items, bucketed via the `content_rating_cs` addon table | Plex `collectionSort=0` ("release"); filter's internal `sort_by: release.desc` → `originallyAvailableAt:desc` | all 18 exist (2 empty: `12+`, `13+`) |
| Not Rated Movies | Movie | dynamic `content_rating` "other" → `smart_filter` | distinct ratings present but not claimed by any of the 18 buckets | same as above | not captured (probe crashed) |
| Age `N`+ Shows (TV subset of 1–18) | Show | same mechanism | TV library's distinct `contentRating` values | same | 15 of 18 exist (3 missing — see Open Questions) |
| Not Rated Shows | Show | same "other" mechanism | as above | same | not captured |
| Ratings Collections | Both | `separator` template | static, no source data | n/a — always blank | 0 items, confirmed live |

`Ratings Collections` is **not a mistake or leftover** — it's Kometa's
"section separator" mechanism (`defaults/templates.yml` → `separator`
template): a permanently-blank placeholder collection whose only purpose is
a visual divider in Plex's alphabetized collection list. Live
title/summary confirm this exactly: name `Ratings Collections`, summary
`"Section separator for Ratings Collections."` (from `en.yml`
`collections.separator`), 0 items always, `blank_collection: true` in its
resolved template (no `placeholder_*` var is set, so `use_blank` defaults to
`true`).

Total live inventory: **31 Kometa collections on Movies + 20 on TV Shows =
51**, built from exactly 3 builder families (`imdb_award`, `imdb_chart`,
`smart_filter`/dynamic `content_rating`) plus 1 non-content separator.

---

## 2. `oscars` — Academy Awards

Source file: `defaults/award/oscars.yml`.

### 2.1 Static collections

```yaml
Oscars Best Picture Winners:
  imdb_award:
    event_id: ev0000003
    event_year: all
    category_filter:
      - best motion picture of the year
      - best picture
      - best picture, production
      - best picture, unique and artistic production
    winning: true

Oscars Best Director Winners:
  imdb_award:
    event_id: ev0000003
    event_year: all
    category_filter:
      - best achievement in directing
      - best director
      - best director, comedy picture
      - best director, dramatic picture
    winning: true
```

`event_id: ev0000003` is IMDb's internal event id for the Academy Awards.
`category_filter` matches against the **lower-cased award category text** as
scraped/dumped by the Kometa-Team community IMDb dataset (category naming
has changed across ~95 ceremonies, hence the multiple variants per award).

### 2.2 Dynamic "year" collections

```yaml
dynamic_collections:
  Oscars Winners Awards:
    type: imdb_awards
    sync: true
    data:
      event_id: ev0000003
      starting: latest-4
      ending: latest
    title_format: Oscars Winners <<key_name>>
```

`meta.py` (`auto_type == "imdb_awards"`, ~line 1064) resolves `starting:
latest-4` / `ending: latest` against the full sorted list of ceremony years
for `ev0000003`, producing the **most recent 5 years** — this is why
production currently shows `Oscars Winners 2022`…`Oscars Winners 2026` (5
collections). This window slides forward automatically as new ceremony
years are added to the dataset; it is **not** a fixed year list.
`sync: true` makes each year-collection a `sync_mode: sync` collection
(diffed and pruned every run — see §6).

### 2.3 Data source and cost

`imdb.py` implements the award lookup as:

1. `git_events_validation` (cached property, `imdb.py:1034`) — **one**
   request to `https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master/event_validation.yml`,
   which lists every event id the community dataset covers and its known
   ceremony years.
2. Because `ev0000003` **is** in that dataset, `git_event(event_id)`
   (`imdb.py:1039`) issues **one** request to
   `https://raw.githubusercontent.com/Kometa-Team/IMDb-Awards/master/events/ev0000003.yml`
   — this single file contains **every** ceremony year's full award/category/
   winner data.
3. All 7 Oscars collections (2 static + 5 dynamic) are then resolved
   **entirely in memory** from that one cached file. No further IMDb
   requests happen regardless of how many category filters or year windows
   are configured.

If an event were *not* in the community dataset, Kometa falls back to
per-year HTML scraping (`get_event_years` + one request per
`event_year` via `base_url/event/{event_id}/{year}/...`) — expensive, but
not the path taken for `ev0000003` in production.

**Total cost per full run: 2 lightweight GitHub raw-file GETs**, cached only
for the lifetime of the process (see §8 — not persisted to Kometa's sqlite
cache across runs).

### 2.4 Sort, artwork, summary

- `template: [shared, arr, custom]`. The `custom` template
  (`templates.yml`) sets `collection_order: custom` — items appear in the
  **order the builder returned them**, not alphabetically. For
  `imdb_award`, that's whatever order `_award()` iterates categories/years
  in (chronological by event year, category order as scraped); the dynamic
  year collections are explicitly `collection_order: release` via
  `use_year_collections` template default... actually per `oscars.yml`
  `template_variables.collection_order.default: release` — **the dynamic
  year collections sort by release date**, while the two static winner
  collections use `collection_order: custom` (dataset order) since they
  come from the plain `custom` template with no override.
- `sort_title` (governs where the *collection itself* sorts among other
  collections, distinct from item order inside it): built from
  `collection_section: 130` + explicit tiebreak `sort: Oscars !1` / `!2` for
  the two static ones.
- Poster: `url_poster` from
  `https://raw.githubusercontent.com/Kometa-Team/Default-Images/.../award/oscars/{best_picture_winner,best_director_winner,winner/<<key>>}.jpg`
  — a **static hosted image per collection**, not generated. Only
  downloaded if no matching local file exists in `/assets/<collection
  name>/poster.*` (production sets `prioritize_assets: true`,
  `asset_folders: true`).
- Summary: translation-driven (see §5).
- Both templates also chain the `arr` template (Radarr/Sonarr
  add-existing/upgrade/monitor hooks) — inert in production; see §9.

---

## 3. `imdb` — IMDb charts

Source file: `defaults/chart/imdb.yml`.

```yaml
IMDb Popular:   imdb_chart: popular_<<library_type>>s   # popular_movies / popular_shows
IMDb Top 250:   imdb_chart: top_<<library_type>>s        # top_movies / top_shows
IMDb Lowest Rated: imdb_chart: lowest_rated               # movie-only; no show equivalent exists
```

`IMDb Lowest Rated` explicitly sets `allowed_libraries: movie` in its
`shared` template call — **this is why there is no `IMDb Lowest Rated` for
TV Shows: the default intentionally omits it, it is not suppressed by
config or missing data.** IMDb itself has no "lowest rated TV" chart to
begin with (`show_charts` in `imdb.py` only lists `popular_shows`,
`top_shows`, `trending_india`).

### 3.1 Data source

`imdb.py` (`chart_urls`, `chart_graphql_map`, `_ids_from_chart`,
`imdb.py:34-61, 869-894`):

| Chart key | Primary source (GraphQL, `api.graphql.imdb.com`) | Count | HTML fallback page |
|---|---|---|---|
| `popular_movies` | `chartTitles(chartType: MOST_POPULAR_MOVIES, first: 100)` | up to 100 | `https://www.imdb.com/chart/moviemeter` |
| `top_movies` | `chartTitles(chartType: TOP_RATED_MOVIES, first: 250)` | up to 250 | `https://www.imdb.com/chart/top` |
| `lowest_rated` | `chartTitles(chartType: LOWEST_RATED_MOVIES, first: 100)` | up to 100 | `https://www.imdb.com/chart/bottom` |
| `popular_shows` | `chartTitles(chartType: MOST_POPULAR_TV_SHOWS, first: 100)` | up to 100 | `https://www.imdb.com/chart/tvmeter` |
| `top_shows` | `chartTitles(chartType: TOP_RATED_TV_SHOWS, first: 250)` | up to 250 | `https://www.imdb.com/chart/toptv` |

Each chart resolves in **one GraphQL POST** to
`https://api.graphql.imdb.com/`; the HTML/xpath scrape against
`www.imdb.com/chart/...` is only used if the GraphQL call throws or returns
zero ids. Production enables 3 movie charts + 2 show charts = **5 chart
fetches per full run, 5 total external requests** (assuming GraphQL
succeeds, which is the expected path).

### 3.2 Item resolution

Each chart returns a list of `tt########` IMDb ids. These are matched
against the library's own **pre-built local ID map** (`plex.py`
`imdb_rating_key_map`, built once per library run from Plex's own GUID data
during the initial full-library scan — no per-item external call). IDs not
present in that map are simply excluded from the collection; with
`show_missing: false` they are also not logged in a "missing" report (that
flag only controls *logging*, not membership — Kometa can only add items it
already owns regardless of `show_missing`). `radarr_add_missing` is a
*separate*, independently-configured flag (unset here, defaults `false`),
so unowned chart entries are not sent to Radarr either.

### 3.3 Sort, artwork, summary

- `template: [imdb_chart, shared, arr, custom]` → `collection_order:
  custom`, i.e. **collection item order matches the chart's own rank
  order** (IMDb's Top 250 rank, Popular Meter rank, etc.), not
  alphabetical.
- Poster: `image: chart/<<style>>/<<mapping_name_encoded>>` (`style: color`)
  → a static hosted image per chart from the same `Default-Images` repo,
  same "local asset wins" precedence as §2.
- `sync_mode: sync` (via `custom` template) — see §6, items that drop off a
  chart are removed from the Plex collection on the next run.

---

## 4. `content_rating_cs` — Common Sense age buckets

Source file: `defaults/both/content_rating_cs.yml`; resolution logic in
`modules/meta.py` (`auto_type in [... "content_rating" ...]`, lines
899–1228 for the general branch, and 835–1238 for the dynamic-collection
scaffolding).

### 4.1 This is a Plex-native smart collection, not an external-API builder

Critical finding, confirmed both from source and from the live probe: for
this family, Kometa's only job is to compute the right **Plex smart-filter
URI** once and hand it to `library.create_smart_collection(...)`
(`builder.py:5250`). From that point on:

- `builder.py:1284` — the entire "gather ids from builders, add/remove
  items" code path is **skipped whenever `builder.smart_url` is set**
  (`if not builder.smart_url and builder.builders and not
  builder.blank_collection:`).
- Membership is evaluated **live by the Plex Media Server** every time
  someone browses the collection or Plex re-indexes; Kometa never adds or
  removes a single item from these collections.
- **Answering the coordinator's question directly: yes, smart collections
  need no membership diff logic in Phase 3 at all.** Creating (or updating,
  if the filter definition changes) the smart collection once is the whole
  job for this family. This materially simplifies the design — no
  polling, no per-item add/remove bookkeeping, no re-evaluation cadence
  beyond "did the desired filter definition change."
- What Kometa *does* re-check each run is whether the **existing** smart
  collection's filter URI still matches the desired one
  (`builder.py:1774-1778`); if it drifted, it calls
  `library.update_smart_collection(self.obj, check_url)` to replace the
  filter definition (still just a metadata write, not a membership
  operation).

### 4.2 How the per-bucket filter is built (data-dependent, not a fixed table)

For `auto_type == "content_rating"` (`meta.py:899-937`):

```python
tags = library.get_tags("contentRating")          # ALL distinct ratings actually present on library items — local Plex query
all_keys = {str(i.title): i.title for i in tags}   # every distinct value seen in the library, key==value
```

Then for each key in `include` (the YAML's static `1`..`18` list):

```python
key_value = [key] if key in all_keys else []
key_value.extend([a for a in addons[key] if a in all_keys and a != key])
```

The `any: {content_rating: <<value>>}` smart filter (from the `smart_filter`
template) becomes an **OR** of `key_value` — i.e. the collection's live
filter is:

> `{key itself, if some item in the library actually carries that literal
> content rating}` **UNION** `{every candidate rating in that bucket's
> `addons` list that some item in the library actually carries}`.

**This is not a static mapping — it is re-derived from whatever distinct
`contentRating` strings currently exist in the library**, intersected
against the static candidate table below. A bucket whose candidates are all
absent from the library gets an empty `value` list; whether that produces a
0-item smart collection (still created, as seen for `12+`/`13+` on Movies)
or no collection at all (as apparently happened for 3 buckets on Shows)
depends on whether `smart_filter`/`ignore_blank_results` treats an empty
`any` list as valid — see Open Questions.

Our bare Common Sense values (`"17"`, not `"17+"`) matter here because the
bucket's own key literal (e.g. `"17"`) is only pulled in if some Plex item
in the library has `contentRating` **exactly equal to the bare string
`"17"`** — which only happens for content whose agent/source assigned a
literal Common Sense-style numeric rating. For most items the bucket is
populated purely through its `addons` (conventional MPAA/TV ratings), not
through the bare number.

### 4.3 Static candidate table (from `content_rating_cs.yml`, applies to both libraries)

| Bucket | Candidate ratings (bucket key + `addons`) |
|---|---|
| 1 | `gb/U`, `gb/0+`, `G`, `TV-G`, `TV-Y`, `E`, `gb/E`, `G - All Ages`, `A`, `no/A`, `no/5`, `no/05`, `01` |
| 2 | same set as 1, with `02` |
| 3 | same set as 1, with `03` |
| 4 | same set as 1, with `04` |
| 5 | same set as 1, with `05` |
| 6 | `gb/U`, `gb/0+`, `G`, `TV-G`, `TV-Y`, `E`, `gb/E`, `G - All Ages`, `no/6`, `no/06`, `no/7`, `no/07`, `06` |
| 7 | `gb/PG`, `TV-PG`, `TV-Y7`, `TV-Y7-FV`, `PG`, `PG - Children`, `no/6`, `no/06`, `no/7`, `no/07`, `07` |
| 8 | same set as 7, with `08` |
| 9 | `gb/PG`, `TV-PG`, `TV-Y7`, `TV-Y7-FV`, `PG`, `PG - Children`, `gb/9+`, `no/9`, `no/09`, `no/10`, `09` |
| 10 | same candidates as 9, no own-number addon (`10` not listed) |
| 11 | `gb/PG`, `TV-PG`, `TV-Y7`, `TV-Y7-FV`, `PG`, `PG - Children`, `gb/9+`, `no/9`, `no/09`, `no/10`, `no/11` |
| 12 | `gb/12`, `gb/12A`, `12+`, `PG`, `PG - Children`, `no/11`, `no/12` |
| 13 | `gb/12`, `gb/12A`, `12+`, `PG-13`, `PG-13 - Teens 13 or older`, `no/11`, `no/12` |
| 14 | `gb/12`, `12`, `gb/12A`, `12+`, `PG-13`, `TV-14`, `PG-13 - Teens 13 or older`, `no/11`, `no/12` |
| 15 | `gb/15`, `gb/14+`, `TV-14`, `PG-13 - Teens 13 or older`, `no/15`, `no/16` |
| 16 | same set as 15 |
| 17 | `gb/18`, `gb/15`, `gb/14+`, `TV-14`, `R`, `R - 17+ (violence & profanity)`, `TVMA`, `TV-MA`, `MA-17`, `no/18`, `no/15`, `no/16` |
| 18 | `gb/18`, `MA-17`, `TVMA`, `TV-MA`, `R`, `gb/R18`, `gb/X`, `X`, `NC-17`, `R - 17+ (violence & profanity)`, `R+ - Mild Nudity`, `Rx - Hentai`, `no/18` |

(Full literal YAML preserved at `scratchpad/kometa/defaults/both/content_rating_cs.yml`.)

### 4.4 Live resolved filter table — Movies (confirmed by direct Plex probe)

| Collection | Live `contentRating` OR-list | Item count |
|---|---|---|
| Age 1+ Movies | `G`, `TV-Y` | 1 |
| Age 2+ Movies | `G`, `TV-Y` | 1 |
| Age 3+ Movies | `3`, `G`, `TV-Y` | 11 |
| Age 4+ Movies | `4`, `G`, `TV-Y` | 12 |
| Age 5+ Movies | `5`, `G`, `TV-Y` | 71 |
| Age 6+ Movies | `6`, `G`, `TV-Y` | 111 |
| Age 7+ Movies | `7`, `TV-Y7`, `PG` | 114 |
| Age 8+ Movies | `8`, `TV-Y7`, `PG` | 93 |
| Age 9+ Movies | `9`, `TV-Y7`, `PG` | 30 |
| Age 10+ Movies | `10`, `TV-Y7`, `PG` | 73 |
| Age 11+ Movies | `11`, `TV-Y7`, `PG` | 121 |
| Age 12+ Movies | `PG` | 0 |
| Age 13+ Movies | `PG-13` | 0 |
| Age 14+ Movies | `14`, `12`, `PG-13`, `TV-14` | 249 |
| Age 15+ Movies | `15`, `TV-14` | 226 |
| Age 16+ Movies | `16`, `TV-14` | 235 |
| Age 17+ Movies | `17`, `TV-14`, `R`, `TV-MA` | 291 |
| Age 18+ Movies | `18`, `TV-MA`, `R` | 67 |

All 18 buckets exist for Movies and match the static candidate table (§4.3)
exactly, filtered down to what the movie library's own distinct
`contentRating` values happen to be — confirming the mechanism in §4.2. No
bucket disagreed with the derivation. Plex `filters.libtype = "movie"`,
`filters.sort = ["originallyAvailableAt:desc"]`, `collectionSort = 0`
("release" — `plex.py`'s `collection_order_keys = {0: "release", 1:
"alpha", 2: "custom"}`) on every bucket, consistent with the `smart_filter`
template's `sort_by: release.desc` default.

**Not captured (probe crashed before reaching it): `Not Rated Movies`, and
the entire TV Shows library.** See Open Questions.

### 4.5 Artwork and summary

- Poster: `image: content_rating/cs/<<key_name>>` (bucket number) or
  `content_rating/cs/NR` (Not Rated) — static hosted image per bucket, same
  `Default-Images` repo, same local-asset-wins precedence.
- Summary and name come from Kometa's central translation file
  (`Kometa-Team/Translations`, `defaults/en.yml`), fetched live and quoted
  verbatim below (§5) — this matches the live probe's summary text exactly,
  e.g. `"Movies that are rated 17 according to the Common Sense Rating
  System."`

---

## 5. Naming and summary source (applies to all three families)

Kometa fetches `https://raw.githubusercontent.com/Kometa-Team/Translations/master/defaults/en.yml`
**once per run** (`config.py`/`meta.py` `self.config.GitHub.translation_yaml("en")`,
cached in-memory for the process), and uses its `collections.<translation_key>`
entries for `name`/`summary` unless a local override is configured. Exact
entries relevant here (fetched live, verbatim):

| `translation_key` | Name | Summary |
|---|---|---|
| `oscars_picture` | Oscars Best Picture Winners | The Academy Award for Best Picture is one of the Academy Awards presented annually by the Academy of Motion Picture Arts and Sciences since the awards debuted in 1929. |
| `oscars_director` | Oscars Best Director Winners | The Academy Award for Best Director is one of the Academy Awards presented annually by the Academy of Motion Picture Arts and Sciences since the awards debuted in 1929. |
| `oscars_year` | Oscars Winners `<<key_name>>` | Academy Awards (Oscars) Winners for `<<key_name>>`. |
| `imdb_popular` | IMDb Popular | List of IMDb Popular `<<library_translation>>`s. |
| `imdb_top` | IMDb Top 250 | List of IMDb Top 250 `<<library_translation>>`s. |
| `imdb_lowest` | IMDb Lowest Rated | List of IMDb Lowest Rated `<<library_translation>>`s. |
| `content_rating_cs` | Age `<<key_name>>`+ `<<library_translationU>>`s | `<<library_translationU>>`s that are rated `<<key_name>>` according to the Common Sense Rating System. |
| `content_rating_other` | Not Rated `<<library_translationU>>`s | `<<library_translationU>>`s that are Unrated, Not Rated or any other uncommon Ratings. |
| `separator` | `<<key_name>>` Collections | Section separator for `<<key_name>>` Collections. |

`<<library_translation>>`/`<<library_translationU>>` resolve to `movie`/`Movie`
or `show`/`Show`.

---

## 6. Refresh / diff semantics

Two entirely different mechanisms, dispatched by whether the collection is
a **smart** collection (`smart_url` set) or a **regular** (list-of-items)
collection:

### 6.1 Regular collections (`oscars`, `imdb` — both use `sync_mode: sync` via the `custom` template)

From `kometa.py` (top-level run loop, ~lines 1264-1349) and `builder.py`
`sync_collection` (~4577):

1. At builder init, `self.remove_item_map = {item.ratingKey: item for item
   in <current Plex collection members>}` — a snapshot of who is currently
   in the collection.
2. As the builder resolves this run's item list and adds each one to the
   collection (`add_to_collection`), any item that was already a member is
   popped out of `remove_item_map`.
3. After adding, if `sync_mode == sync` (true here via the `custom`
   template), `sync_collection()` removes every item **still left** in
   `remove_item_map` — i.e. every current member that the fresh builder run
   did **not** re-select.
4. **Yes — items that fall off an IMDb chart, or lose their award-winner
   status (dataset correction), are removed from the Plex collection on the
   very next run.** This only removes *collection membership*, never the
   underlying Plex library item.

### 6.2 Smart collections (`content_rating_cs`)

No add/remove loop runs at all (§4.1). Kometa only ever (a) creates the
smart collection if missing, or (b) rewrites its filter URI if the desired
filter changed. Plex itself re-evaluates membership on every access.

---

## 7. Collection deletion safety

Traced directly in the top-level run loop, `kometa.py` (module copied to
scratchpad root — Kometa's real entrypoint, not autoposter code):

```python
final_collection_count = collection_count_after_run(builder.beginning_count, items_added, items_removed)
if builder.build_collection and not builder.blank_collection and final_collection_count < builder.minimum:
    logger.info(f"{builder.Type} Minimum: {builder.minimum} not met for {mapping_name} Collection")
    valid = False
    if builder.details["delete_below_minimum"] and builder.obj:
        logger.info(builder.delete())
        library.stats["deleted"] += 1
```

Key facts:

- `minimum_items` defaults to **1** globally (`config.py:838`,
  `check_for_attribute(..., default=1)`), matching the production brief's
  expectation. Falling below it just means the collection isn't
  created/updated **this run** (`valid = False` skips
  `builder.load_collection()`) — poster/summary/sort are simply not
  reapplied that pass.
- **Deletion only happens if `delete_below_minimum` is `true`**, and its
  default is **`false`** (`config.py:840`). Production does not set it, so
  it is `false`. **A Kometa-managed collection is never silently deleted
  for falling below `minimum_items` under the production configuration or
  Kometa's own defaults.** This matches the live evidence: `Age 12+
  Movies` and `Age 13+ Movies` sit at 0 items and still exist.
- Separately, `delete_collections_named` (a literal list of collection
  names) and `delete_not_scheduled` (deletes a collection if its
  `schedule:` condition is no longer met) are independent opt-in deletion
  paths — neither is triggered by anything in the production config for
  these three defaults.
- **Design implication for Phase 3: default to never deleting a collection
  automatically.** Only delete on an explicit, unambiguous signal (e.g. the
  collection is no longer configured at all AND carries our ownership
  label), and treat "0 items" as a normal, expected, permanent state for
  low-population smart buckets — not a signal to clean up.

---

## 8. Rate / traffic profile

For one full pass of exactly the production-enabled defaults (`oscars` +
`imdb` on Movies, `imdb` + `content_rating_cs` on both libraries),
**against real external providers**:

| Provider | Calls | What |
|---|---|---|
| IMDb GraphQL (`api.graphql.imdb.com`) | 5 | 3 movie charts + 2 show charts, one POST each, returns 100–250 ids |
| GitHub raw (`Kometa-Team/IMDb-Awards`) | 2 | `event_validation.yml` once, `events/ev0000003.yml` once — covers all 7 Oscars collections |
| GitHub raw (`Kometa-Team/Translations`) | 1 | `defaults/en.yml`, covers every collection's name/summary this run |
| GitHub raw (`Kometa-Team/Default-Images`) | ≤31 movie + ≤20 show, only for collections without a matching local `/assets` override | Static poster jpgs, one GET per collection (skipped entirely if `prioritize_assets`+local file found) |
| **TMDb / Trakt / MDBList** | **0** | None of `oscars`, `imdb`, `content_rating_cs` invoke these builders |
| Plex (local) | 1 full-library metadata scan per library (already needed for guid/id indexing) + smart-collection create/update calls | All local, not "external" traffic |

**`content_rating_cs` costs zero external requests** — it is 100% derived
from Plex's own already-indexed `contentRating` field plus one shared
translation-file fetch.

Critically, **none of the IMDb chart/award data is persisted across runs**
by stock Kometa — `cache.py` has no `imdb_chart`/`imdb_award` table; the
`self._web_events`/`self._git_events` caches on the `IMDb` object are
in-memory only and die with the process. The sqlite cache (`cache: true`,
`cache_expiration: 60`) covers per-item lookups (`imdb_to_tmdb_map`,
`imdb_parental`, `imdb_keywords`), not these three collection families.
**Every full Kometa run re-fetches all 5 IMDb charts and both GitHub
Oscars/translation files from scratch — the traffic above is per-run, not
amortized.** This is exactly the behavior Phase 3 is meant to eliminate:
these 8 external requests are already cheap in isolation, but a proper
implementation should cache the raw chart/award/translation payloads with
a TTL (the "60 minutes" `cache_expiration` convention already used
elsewhere) rather than re-fetching them unconditionally every scheduled
run.

---

## 9. `add_existing` / Arr sync

`radarr.py`/`sonarr.py`/`builder.py` (`add_missing`, `add_existing`,
`upgrade_existing`, `monitor_existing` — all independent booleans, default
`false`):

- `add_missing`: for collection entries **not** owned by the Plex library,
  ask Radarr/Sonarr to add+search for them.
- `add_existing`: for collection entries that **are** in Radarr/Sonarr's
  database but weren't picked up by the normal library scan path, apply
  tagging/monitor state (does not trigger a download search by itself —
  that's `upgrade_existing`/`monitor_existing`+`radarr_search`).
- All three `arr` template collections (`oscars`, `imdb` — via `template:
  [..., arr, ...]`) resolve these flags from the **library-level**
  Radarr/Sonarr config (`library.Radarr.add_existing`, defaulting to
  `false` unless the top-level `radarr:`/`sonarr:` block or a
  per-library override sets it).
- **Production does not configure `add_existing` anywhere in the visible
  settings** (per the brief), and no Radarr/Sonarr connection block was
  supplied in the ground-truth materials for this task — so under the
  library defaults, `add_existing` resolves to `false` for all three
  collection families. **This feature currently does nothing in
  production.** If "Kometa `add_existing` parity" is a stated Phase-3 goal,
  it needs its own config surface (Radarr/Sonarr connection + explicit
  `add_existing: true`) that does not exist yet in the production Kometa
  config as described — flagged as a design gap, not a bug in this
  research.

---

## 10. Open questions

1. **TV Shows: full live data unconfirmed.** The coordinator's Plex probe
   crashed (`TypeError` in `plexapi`'s `_parseFilters`, apparently on a
   collection with `content=None`) immediately after dumping the 18 Movie
   CS buckets, before reaching `Not Rated Movies`, the Oscars/IMDb Movie
   entries' sort/summary/live-filter data, or **any** TV Shows data. I
   attempted to fix and re-run an equivalent probe (found working
   credentials + `plexapi` already available locally), but the run was
   blocked by the environment's safety classifier before it could execute
   (writing/using a live production token is outside this research task's
   sandboxed permissions). Everything about TV Shows in this document
   (which 15 of 18 CS buckets exist, their exact resolved OR-lists, why 3
   are missing, `Not Rated Shows`' filter) is derived from source-code
   mechanism only, not confirmed against the live server. **Before Phase 3
   implementation, re-run a live probe against the Shows library** (fixing
   the crash: guard `col.filters` access in a try/except per-collection, as
   my `probe_collections_full.py` in the scratchpad already does) to get
   the ground truth.
2. **Which 3 of the 18 Show CS buckets are missing, and why exactly 3?**
   My working theory (§4.2/4.4): those buckets' entire candidate list (key
   + addons) has zero matches against the TV library's distinct
   `contentRating` values, and an empty `any` list either fails smart-filter
   creation or is filtered out before `create_smart_collection` is called —
   unconfirmed from source without stepping through `library.py`'s smart
   filter builder in more detail than time allowed here.
3. **`Not Rated Movies` / `Not Rated Shows` filter contents** — the
   mechanism is documented (§4.2's "other" branch: every distinct rating
   present in the library that isn't consumed by any of the 18 buckets),
   but the exact live OR-list was not captured.
4. **Radarr/Sonarr connection details** were not in scope/available for
   this task — `add_existing`'s "currently does nothing" conclusion (§9)
   assumes no `radarr:`/`sonarr:` block sets it `true` at the top level;
   this should be double-checked against the actual production
   `config.yml` before treating it as settled.
5. **Exact asset-folder poster override behavior** (`prioritize_assets`,
   `asset_folders`, `download_url_assets: false` by default) was read from
   config flags and cross-referenced narratively but not traced
   line-by-line through `poster.py`/`operations.py`'s asset-resolution
   order; if Phase 3 needs byte-exact precedence rules (e.g. does a locally
   present poster ever get re-downloaded/overwritten), that needs a
   follow-up pass through `operations.py`'s asset-handling functions.

---

## Addendum: TV Shows verified live (closes open questions 1-3)

The earlier probe crashed part-way through Movies, so the Shows side was unverified. It has now
been re-run successfully against production. Full dump (one JSON object per collection, both
libraries) is in the session scratchpad as `plex-collections.txt`.

**19 Kometa-labelled collections in TV Shows**, with their live smart-filter contents:

| Collection | n | `contentRating` matched |
|---|---|---|
| Age 2+ Shows | 7 | `2` |
| Age 3+ Shows | 5 | `3` |
| Age 5+ Shows | 1 | `5` |
| Age 6+ Shows | 2 | `6` |
| Age 7+ Shows | 5 | `7` |
| Age 8+ Shows | 3 | `8` |
| Age 9+ Shows | 2 | `9` |
| Age 10+ Shows | 18 | `10` |
| Age 11+ Shows | 3 | `11` |
| Age 13+ Shows | 13 | `13` |
| Age 14+ Shows | 108 | `14`, `12` |
| Age 15+ Shows | 44 | `15` |
| Age 16+ Shows | 30 | `16` |
| Age 17+ Shows | 0 | `TV-MA` |
| Age 18+ Shows | 0 | `TV-MA` |
| Not Rated Shows | 10 | `tmdb` |
| IMDb Popular | 28 | (not smart) |
| IMDb Top 250 | 38 | (not smart) |
| Ratings Collections | 0 | (not smart) |

**Q2 — which Show buckets are missing, and why. RESOLVED.** Three: `Age 1+`, `Age 4+` and
`Age 12+ Shows`. Movies has all 18 buckets, Shows only 15. The dynamic `content_rating` builder
creates a bucket only for values that actually occur in that library, so the three absent buckets
simply have no shows carrying those ratings. This also explains why `Age 17+`/`Age 18+ Shows`
exist while matching **0** items: they were created when such shows were present, and
`delete_below_minimum` defaults to `false`, so they persist afterwards. Empty is normal; do not
treat an empty bucket as a defect or delete it.

**Note the Shows filters are much simpler than the Movies ones.** Movies buckets OR the bare
Common Sense value together with equivalent conventional US ratings (`Age 17+ Movies` matches
`17`, `TV-14`, `R`, `TV-MA`), whereas most Shows buckets match only the bare value. Do not assume
a shared mapping table between the two libraries — derive each per library from the live filters.

### A real data-quality problem, flagged for the operator

**`Not Rated Shows` matches `contentRating == "tmdb"`, and 10 shows currently match it.**

That is a literal string `"tmdb"` sitting in those shows' content-rating field. The production
Kometa config carries this warning against `mass_content_rating_update`:

> NOTE: tmdb is NOT a valid source here - kometa mass-writes unknown values as literal strings.

So this is that failure mode, already realised on 10 shows. It is a pre-existing data defect, not
something this project introduced, but it matters here for two reasons:

1. Those 10 shows will render a **Common Sense badge reading "tmdb"** unless the badge layer
   rejects non-numeric content ratings.
2. Phase 2a's metadata operations write `contentRating` from MDBList Common Sense. On the first
   run with writes enabled, these 10 shows will either be corrected (if MDBList has a Common Sense
   rating for them) or left as-is (if it does not).

Recommend surfacing the list of affected shows to the operator before the Phase 2a cutover rather
than silently rewriting or silently preserving it.
