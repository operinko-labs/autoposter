# Collection data provenance

## `content_rating_cs.json`

Generated from `/defaults/both/content_rating_cs.yml` in the pinned Kometa image
(`kometateam/kometa` v2.4.8, digest `sha256:c58f6d4a...`) — the exact digest the production
CronJob runs.

Holds the 18 age buckets, each bucket's candidate content-rating list (`addons`), the title
format `Age <<key_name>>+ <<library_typeU>>s`, and the catch-all name
`Not Rated <<library_typeU>>s`.

**The bucket filter is data-dependent, not a fixed table.** For each bucket, the live Plex
smart filter is the bucket's own key (only if some library item literally carries that bare
rating) plus every candidate in its `addons` list that some library item actually carries.
Candidates absent from the library are omitted. So the same bucket produces different filters
on different libraries.

### Validated against production

Deriving the filters this way and comparing against the live Plex collections:

| Library | Bucket | Derived | Live | |
|---|---|---|---|---|
| Movies | 17 | `17, R, TV-14, TV-MA` | `17, R, TV-14, TV-MA` | match |
| TV Shows | 14 | `12, 14` | `12, 14` | match |
| TV Shows | Not Rated | `tmdb` | `tmdb` | match |
| TV Shows | 17 | `17` | `TV-MA` | **mismatch** |

The TV Shows bucket 17 mismatch is worth understanding before relying on it. `TV-MA` is not
currently present anywhere in the TV library while `17` is, so the live filter appears to be
stale — created when `TV-MA` was present and not since updated. Our derivation is arguably
the more correct of the two, but confirm against a fresh Kometa run before assuming so.

Two other things the validation surfaced:

- **Finnish ratings are in no bucket.** The Movies library carries 15 unclaimed values, of
  which `fi/K-3` through `fi/K-18` and `fi/S` are the bulk. They all fall into
  `Not Rated Movies`, which is why that collection holds 28 items. This is faithful to
  Kometa, not a defect — but it does mean Finnish-rated films get no age bucket.
- **`tmdb` is the sole unclaimed value in TV Shows**, the literal string mass-written into
  ten shows' `contentRating` by an earlier misconfiguration. `Not Rated Shows` exists purely
  to collect them.
