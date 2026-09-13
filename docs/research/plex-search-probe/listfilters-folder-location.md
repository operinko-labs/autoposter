# `listFilters` and the folder filter — the row-176 pre-code probe

**Date:** 2026-09-06 · **Rows:** 176, and 177's open question answered in the
same payload · **Mode:** read-only, two GETs.

## Why

`folder_location` is the one attribute in Kometa's whole search grammar whose Plex field is
a function call rather than a table entry: `Library.get_search_key`
(`modules/plex.py:1286-1297`) reads `listFilters` at RUN time and takes the first filter
whose `filter` is `source` **or** whose displayed title folds to `folder_location`. The
field is therefore a property of THIS server, and a transcription that hard-coded `source`
would be exactly the mistake the row was filed to avoid. The row requires the read before
any code.

## Read-only proof

```
GET /library/sections
GET /library/sections/{key}/all?includeMeta=1&includeAdvanced=1&X-Plex-Container-Start=0&X-Plex-Container-Size=0
```

That second key is the one plexapi's own `_loadFilters` builds
(`plexapi/library.py:889-894`), so this is the same read `listFilters` makes without
importing plexapi. `Size=0` returns the schema and no items. No `edit(`, no `addLabel`, no
`removeLabel`, no `upload`, no `delete`, no `refresh`. The token travels in an
`X-Plex-Token` header and is never printed; the script prints filter KEYS and TITLES and one
boolean — no media title, no item count, no URL.

## The command

Run against the production pod, which already holds both the token
(`AUTOPOSTER_PLEX_TOKEN`) and the config file naming the server (`AUTOPOSTER_CONFIG`):

```bash
kubectl -n media exec deploy/autoposter -- python -c '...'   # the script in the plan,
                                                            # step 2
```

## Result

```
sections status 200
Movies movie status 200
  keys ['actor', 'atmos', 'audioCodec', 'audioLanguage', 'audioLayout', 'collection', 'contentRating', 'country', 'decade', 'director', 'dovi', 'duplicate', 'editionTitle', 'genre', 'hdr', 'hdr10plus', 'inProgress', 'label', 'location', 'producer', 'resolution', 'studio', 'subtitleCodec', 'subtitleLanguage', 'unmatched', 'unwatched', 'videoCodec', 'writer', 'year']
  folder [('location', 'Folder Location')]
  has_audioCodec True
TV Shows show status 200
  keys ['actor', 'collection', 'contentRating', 'country', 'director', 'editionTitle', 'genre', 'label', 'network', 'producer', 'studio', 'unmatched', 'unwatchedLeaves', 'writer', 'year']
  folder []
  has_audioCodec False
TV Shows season status 200
  keys []
  folder []
  has_audioCodec False
TV Shows episode status 200
  keys ['atmos', 'audioCodec', 'audioLanguage', 'audioLayout', 'collection', 'dovi', 'duplicate', 'hdr', 'hdr10plus', 'inProgress', 'location', 'resolution', 'subtitleCodec', 'subtitleLanguage', 'unmatched', 'unwatched', 'videoCodec', 'year']
  folder [('location', 'Folder Location')]
  has_audioCodec True
```

## What it settles

- **The folder filter's key on this server** is `location`, titled `Folder Location`, on the
  movie libtype and on the episode libtype. Which of `get_search_key`'s two clauses fires —
  `f.filter == "source"` or the folded title — is recorded above by the pair itself: the key
  is `location`, not `source`, so it is the **folded-title clause** that fires on this
  server, not the literal-`source` clause. Every place downstream of this probe that the
  plan wrote `source` as a placeholder is substituted to `location` per the plan's
  substitution rule.
- **The show libtype** reports no folder entry (`folder []`). Kometa re-scopes to `episode`
  unconditionally when the library is a show and the search type is `show`
  (`modules/plex.py:1288-1289`), so the show libtype's answer does not change the field this
  service sends either way; it is recorded because it is what the re-scope is FOR.
- **The season libtype** reports no filters at all (`keys []`, `folder []`). This is the one
  case where `builder_level: season` makes the search type differ from the library kind, and
  it is why the season path has its own refusal rather than a silent fallback.

## Row 177, answered in the same payload

`has_audioCodec` is `True` for the movie libtype and `True` for the episode libtype.
Roadmap row 177 (`audio_codec`) says its first step is a probe and not code — this is that
probe's answer, recorded here so row 177 starts from a measurement. Row 177 is NOT shipped
on this branch; only its answer is written down.

## What it does NOT settle

The VALUES behind the filter. This probe reads the schema (`Size=0`) and never enumerates a
folder's contents; `listFilterChoices` is a separate read that the resolver makes per pass,
already shipped, and this row reuses it unchanged.
