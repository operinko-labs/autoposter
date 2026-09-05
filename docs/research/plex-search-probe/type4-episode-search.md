# `type=4` with an `episode.`-scoped predicate — the E-2 pre-golden probe

**Date:** 2026-09-05 · **Row:** 173 E-2 (facts C5) · **Mode:** read-only, one GET.

## Why

Nothing in this repository had ever fetched a `type=4` search URL from a real
server. The 9b probe (`README.md` in this directory) covered `type=1` and
`type=2`; the 2026-09-04 probe (row 173 E-1, facts C4) asked
`listFilterChoices`, not a search. The season/episode sort matrices and the
oracle goldens that follow them are transcriptions, and C5 requires the shape
be observed before a golden is pinned: Kometa renders
`?type=4&...&episode.title%3C=Pilot` on a SHOW library — the search type is the
`builder_level`, the field scoping is the LIBRARY's kind — and that pairing is
the one thing a transcription cannot prove about itself.

## Read-only proof

One helper, two GETs, no plexapi:

```
GET /library/sections
GET /library/sections/{key}/all?type=4&sort=titleSort&limit=5&episode.title%3C=Pilot
```

No `edit(`, no `addLabel`, no `removeLabel`, no `upload`, no `delete`, no
`refresh`. The token travels in an `X-Plex-Token` header and is never printed;
the script prints three scalars and no titles.

## The command

Run against the production pod, which already holds both the token
(`AUTOPOSTER_PLEX_TOKEN`) and the config file naming the server
(`AUTOPOSTER_CONFIG`):

```bash
kubectl -n media exec deploy/autoposter -- python -c '...'   # the script in
                                                            # the plan, Task 1
                                                            # Step 1
```

## Result

```
status 200
count 5
first_type episode
```

## What it settles

- Plex answers `type=4` on a show section: the query is legal, not a 400.
- The `episode.`-scoped predicate narrows rather than erroring, so
  `show_translation` applying by the LIBRARY's kind while `type=` comes from
  the search level is the real server's behaviour and not only Kometa's code
  path (`modules/builder.py:4176-4181` vs `:4093-4121`).
- The elements returned carry `type="episode"`, which is what makes
  `BuilderResult(level="episode")` → `owned_index("episode")` →
  `resolve_external` the correct chain: the rating keys the search returns are
  episode rating keys, indexed by `section.search(libtype="episode")`.

## What it does NOT settle

The sort matrices themselves. Those are transcribed from Kometa v2.4.8
`modules/plex.py:668-715` and pinned by value against the vendored driver
(`tests/test_collection_search_sorts.py`), which is a different guarantee and
is why this probe carries `sort=titleSort` — a name both tables hold — rather
than an episode-only sort.
