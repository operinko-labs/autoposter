# Member sort — a list source's order as the library's title order

**Date:** 2026-09-12
**Status:** Implemented — roadmap row 269 records what shipped
**Roadmap:** row 269; builds on row 268

## 1. Purpose

Plex sorts a library by `titleSort`, a string. A franchise or a curated list
therefore appears in alphabetical order, never in the order its source lists
it. Row 268 fixed this for one source, the TMDb franchise collection a movie's
own record points at. The operator's rule generalises it:

> The sort order should be whatever comes from the list source (be it tmdb,
> imdb, radarr, etc user-supplied media sorter source).

and, on the original note's "instead of generating a collection out of it":

> It should be able to run without generating a collection at all, yes.

So: any list-shaped collection definition this service can already build can
declare that its members' sort titles follow its order, and can do so without
creating a Plex collection.

## 2. Decisions carried in from the operator

1. **Order is the source's, verbatim.** Nothing re-sorts a builder's output;
   `limit` truncates it, filters subset it, and the surviving order is the
   position. Row 268 already dropped its release-date sort for this reason.
2. **A franchise stays in its own alphabetical slot.** The value is
   `"<base> <NN>"` with the base filed at its own letter. There is no `!NN_`
   prefix and never will be; a member whose own title starts elsewhere moves
   to the base's slot, which is the only way non-adjacent items can be
   sub-sorted.
3. **Sort-only is a real mode.** A definition may record positions and create
   no Plex collection.

## 3. Config

Three keys on `CollectionDefinition` (`config/schema.py`), all optional:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `member_sort` | bool | `false` | Record each resolved member's position in this definition's order for the pipeline to write as its sort title. |
| `create_collection` | bool | `true` | Create and reconcile the Plex collection as today. `false` runs the definition for its item-level effects only. |
| *(none)* | | | The base name is derived from the definition's `title` by `facts/franchise_sort.py`'s existing rule: trailing " Collection" and a leading `The`/`A`/`An` stripped. No `member_sort_base` key in this cut; a definition that wants a different base retitles itself, which `create_collection: false` makes free. |

Validation, at config load:

- `member_sort: true` on a **smart** builder (`dynamic`, `smart_filter`,
  `smart_url`, `smart_label`) is refused: a smart definition hands Plex a
  filter and never holds an ordered member list. Registered in
  `_SMART_REFUSABLE_DEFAULTS` so the existing
  `_smart_definitions_refuse_what_they_cannot_apply` validator carries it.
- `create_collection: false` without `member_sort: true` is refused: a
  definition that does nothing must not load as configured. (`item_label` is
  the other item-level effect a definition has; opening `create_collection:
  false` to a label-only definition is a one-line relaxation later, not part
  of this cut.)
- `member_sort` and `create_collection` are added to `_REFUSED_PLAYLIST_FIELDS`
  with reasons: a playlist's order is already Plex's own custom order, and a
  playlist has no collection to not create.
- Both keys join `engine._INHERITED_BY_EXPANSION`, so a family placeholder
  (`facts_family`, the `content_franchises` pack) passes them to every unit.
  The base is then each unit's own title, which for the franchise pack is
  TMDb's collection name with the pack's `remove_suffix` already applied.
- Neither key joins `lists._RIDE_ALONG_DEFAULTS`: they do not change the Plex
  collection's payload, and the members hash must not move for a definition
  that only turned member sort on.
- `tests/test_example_config_matches_schema.py` gains the keys through the
  example config block.

The write side keeps row 268's two `operations` keys and gives the source key
a second value: `operations.sort_title_source: collections` reads recorded
positions; `tmdb_collection` keeps its zero-config behaviour. One `Literal`,
so exactly one source is active per library, and the explicit-source law
(no default source, no fallback chain) holds. `operations.sort_title_apply`
gates the write as it does today.

## 4. Data model

A new table, `item_sort_positions`, one row per item at most:

| Column | Type | Notes |
|---|---|---|
| `item_id` | int, PK | FK `media_items.id ON DELETE CASCADE`, the row 99 shape. |
| `library` | str, indexed | The library the collections pass wrote it under, so a pass can replace one library's rows. |
| `definition_title` | str | The definition that owns this item this pass, for the log and the item page. |
| `base` | str | The derived base name, stored so the pipeline needs no config lookup and a retitled definition moves the value on the next pass. |
| `position` | int | One-based, in the definition's order after `limit`. |
| `total` | int | Member count after `limit`, so the pipeline pads to `max(2, len(str(total)))` exactly as row 268 does. |
| `recorded_at` | timestamptz | Set on every write. |
| `released_at` | timestamptz, nullable | NULL while the item holds a position. Set when the item leaves its list (§8); the row is then a tombstone the pipeline acts on once and deletes. |

One row per item is the ownership rule made structural (§6): the pass decides
the owner, the table cannot hold two. Nothing is stored on `item_facts`, for
row 99's reason — that row is the provider's record.

Alembic: one hand-numbered revision after the current head
`b7c4e1a92f30`, create and drop only, using `a4db89c94eaa_item_credits.py`
as the template. Upgrade, downgrade and re-upgrade are proven in the
lane's scratch database (the test image has no `psql`; go through the
`postgres` service).

## 5. The collections pass

Inside `engine._run_one`, at the single point that holds a definition's
ordered, filtered, capped members (`items` after the `limit` slice, today
`engine.py:938-943`):

1. If `definition.member_sort`, append `(rating_key, position, total, base)`
   for each item to a per-library assignment list carried on the run cache.
   `ratingKey` off a resolved plexapi item is reload-safe (the tier-2
   enrichment already relies on it).
2. If `definition.create_collection` is false, skip the `preview` counts and
   the `reconcile_list_collection` call entirely and append one action:
   `"%r: sort-only; recorded %d member position(s), no collection created"`.
   `outcome.skipped` keeps its meaning (`not items`). No `managed_collections`
   row is written, so the orphan sweep never sees a row to delete; the
   definition's title still counts as managed in `definition_titles_for`, so a
   collection that existed under that title before the switch is protected
   rather than swept — the same "sync stops at the object" posture as
   everywhere else, and disclosed in the row.
3. After every definition in the library has run (in `run_library`, before
   the sweep), commit the library's assignment in one place:
   - resolve rating keys to `media_items.id` with the `facts_read.py` join
     shape (`MediaItem.library == library, MediaItem.rating_key.in_(...)`);
     a member with no `media_items` row yet (imported but never processed)
     is logged once per pass with its count and skipped — it gets a row on
     its first `process_item`, and the next pass positions it;
   - apply the ownership rule (§6) to collapse the list to one entry per item;
   - upsert the survivors (`ON CONFLICT (item_id) DO UPDATE`, clearing
     `released_at` if a released item came back);
   - **release** every held row for this library whose `item_id` is not
     among the survivors AND whose owning definition *settled* this pass
     (§9): set `released_at = now()`, keep `base`/`position`/`total` for the
     log. A definition that dropped `member_sort`, was removed from config,
     or ran and no longer lists the item, releases it; a definition that
     failed, was outside its schedule, or produced no members at all does
     not (§9). Rows already released are left for the pipeline;
   - enqueue `process_item` for every item whose row was inserted, whose
     `(base, position, total)` changed, or which was released this pass,
     through `queue.jobs.enqueue_batch` under a `dedupe_key` per rating
     key, so the change lands minutes after the pass rather than at the
     next drift sweep. Unchanged rows enqueue nothing.
   `dry_run` (`collections.apply_to_plex` off) skips the upsert, the release
   and the enqueue, and reports the would-be counts, matching how every other
   write in the pass behaves under it.

Preview mode (`run_library(preview=True)`) records nothing and reports the
position count it would write.

## 6. Ownership

An item in two `member_sort` definitions has one sort title. **The first
definition in `library_definitions` order wins** — built-ins first, then
`collections.definitions` in the order the operator wrote them, expanded units
in expansion order. The loser is logged once per pass per definition, with
the count and the winning title, never per item. A load-time refusal was
rejected: the overlap is a property of the lists' contents on the day, which
the config cannot see, and refusing would punish an overlap the operator may
not know exists.

Row 99's per-item `sort_title` override and row 87's `{sort_title: lock}` verb
keep precedence over the recorded position, for free: the pipeline writes the
position through `GatheredFacts.sort_title`, which `plan_edits` already
subtracts overridden and verbed fields from (row 268's branch is the writer).

## 7. The pipeline

`render/pipeline.py::apply_metadata`, after `persist_facts`: when
`operations.sort_title_source == "collections"` and the item is a movie or a
show (a list may hold either, and `titleSort` is writable on both), read the
item's `item_sort_positions` row **by media item id** in `apply_metadata`,
which holds it (never by the server's key, which the Jellyfin identity
migration replaces), and derive `f"{base} {position:0{width}d}"` with
`width = max(2, len(str(total)))`. No row means no value and no write, exactly
as a movie in no franchise today. `sources["sort_title"] = "collections"`.

A **released** row (`released_at` set) yields the empty string instead of a
value: `GatheredFacts.sort_title = ""` means "clear", and `is_empty()` tests
`sort_title is not None` so a clear still reaches `apply_facts`. The writer
branch sends `titleSort.value = ""` with `titleSort.locked = 0` in the same
batched edit as everything else — the shape row 87's `remove` verb already
sends for its clearable fields — when Plex reports the field locked or
holding a value; nothing when it is already blank and unlocked. Plex then
derives the sort title from the title again, which is what clearing the Sort
Title field in Plex Web does through the same PUT. After `apply_facts`
returns with the clear applied, `apply_metadata` deletes the tombstone, so
the clear is written exactly once and the second pass is steady. Under
`sort_title_apply` off the tombstone stays (the write was only reported);
for an item `exemption_reason` exempts, the tombstone is deleted without a
write, row 99's own ruling for its DELETE endpoint, and disclosed the same
way.

The write goes through row 268's `plan_edits` branch, the apply gate, the
exemption gate and the lock. One change there: the branch's kind guard widens
from `movie` to `movie`/`show`. The guard exists because `sort_title` sits in
every kind's `WRITABLE_BY_KIND` set for the override path; the per-source
kind rule lives at the gather, which never sets the value for a show under
`tmdb_collection` (its latch and warning are unchanged) and sets it for
either kind under `collections`. Seasons and episodes stay out at both ends.

## 8. What a departed member looks like

Operator rule: "a member that leaves a list should not keep its last locked
sort position; the sort position should be removed as well, which would
return it to however Plex wants it."

An item that leaves its list, or whose definition dropped `member_sort` or
was removed, is *released* by the pass (§5) and cleared by the pipeline (§7):
one write blanks `titleSort` and unlocks it, Plex regenerates the sort title
from the title, and the tombstone is deleted. Nothing is restored from a
backup and no agent refresh is called; "however Plex wants it" is Plex's own
derivation, reached by handing the field back.

Two consequences, stated so they are chosen rather than discovered:

- **Off-switch order.** Removing `member_sort` from definitions (or the
  definitions themselves) releases every member on the next pass, and the
  pipeline clears them. Unsetting `operations.sort_title_source` first would
  stop the pipeline reading rows at all, leaving the values in place; the
  example config says which to do first. `tmdb_collection` (row 268) has no
  release path of its own — its membership is TMDb's, not a definition's —
  and keeps row 268's stated no-undo; a deployment that moves from it to
  `collections` gets releases through the franchise pack like any list.
- **A sort title the operator set by hand before this service touched the
  item is not preserved.** The clear returns the field to Plex's derivation,
  not to a prior manual value; row 86's metadata backup is the record of
  what was there.

**Live verification owed:** that a `titleSort.value=""` + `titleSort.locked=0`
PUT makes the server re-derive the sort title is Plex Web's behaviour on
clearing the field, pinned here against fakes; the first deployment turning
this on should check one released item in Plex.

## 9. Error handling

- **A definition releases members only when it settled.** Settled means it
  ran this pass and resolved at least one member. A builder failure, a 429
  window, a schedule gate, a source that returned nothing, or a filter that
  excluded everyone leaves that definition's existing rows exactly as they
  were: neither re-recorded nor released. A transient outage must not strip a
  franchise's sort titles and then rewrite them a pass later, and "the source
  returned no items" is already the case the collection reconciler refuses
  to act on (`lists.py`'s "leaving the collection untouched"). The cost is
  that a list which genuinely emptied never releases its members until the
  definition is removed — the same posture the collection object takes, and
  disclosed. The pass logs, per unsettled definition, how many rows it left
  alone.
- The assignment commit is one transaction per library; a failure there is
  logged with the library and the exception class, the library's rows are
  left as they were, and the rest of the pass is unaffected.
- The enqueue is best-effort after the commit; a failure logs and the drift
  sweep or the next full pass carries the value.

## 10. Testing

Through the real entry points, per the standing law for gated features:

- Config: the three refusals (smart builder, `create_collection: false`
  alone, playlist keys), expansion inheritance onto family units, the example
  config round-trips.
- Engine: positions follow builder order after filter and limit; `sort`
  does not reorder; first definition wins with one log line; a departed
  member is released, a retitled definition moves the base, a released item
  that returns is re-held; a failed, gated, empty or filter-emptied
  definition releases nothing; sort-only creates no Plex collection and no
  `managed_collections` row and reports the action; `dry_run` and `preview`
  write nothing; the enqueue fires for inserted, changed and released rows
  only.
- Pipeline: `sort_title_source: collections` reads the row and the trio
  through `apply_metadata` — gate-off byte-identical, gate-on writes
  `titleSort` once, second pass steady; a show member is written; a row 99
  override wins. The release trio: a tombstone writes one blank-and-unlock
  edit and is deleted, the second pass is steady; under apply-off the
  tombstone survives and nothing is written; an exempt item's tombstone is
  deleted without a write.
- Migration: up, down, up in a scratch database.

## 11. Out of this cut, by name

- Restoring a departed member to a *prior manual* sort title rather than to
  Plex's derivation (§8).
- A release path for row 268's `tmdb_collection` source (§8).
- A `member_sort_base` key; retitle the definition instead.
- `create_collection: false` for a label-only definition.
- Smart builders: no ordered output exists to record.
- Kometa's `add_missing` / Radarr feeder family stays a non-goal; this
  sort-only mode shares nothing with it beyond the idea of a definition that
  creates no collection.

## 12. Rollout

Stacked on row 268's branch (`feat/franchise-sort-titles`, PR #233); merge
order is #233 first, then this. Both defaults are off, so a deployment that
changes no config gets no new table rows, no new reads and no new writes; the
migration is the only thing that runs unasked.
