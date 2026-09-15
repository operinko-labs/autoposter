# Per-server outcomes and catch-up

**Status:** approved design, pre-implementation. Written 2026-09-13.

**Depends on:** the Jellyfin media-server work (`2026-09-12-jellyfin-media-server-design.md`, shipped in v0.3.0). Sibling of `2026-09-13-servers-and-settings-design.md`, whose Servers tab carries this design's buttons.

## 0. Summary

On the first production full pass with Jellyfin configured, every metadata write to Jellyfin failed and nothing recorded it: the per-server write logs a warning and returns, the job ends as success, and no row anywhere says the server still lacks the item's metadata. The failures healed on the next full pass, because the writer plans edits against what the server holds, but they were invisible in between and would have stayed silent had the cause persisted.

This design records metadata outcomes per item and server the way artwork deliveries are recorded, retries both per server at the existing pass's cadence with a bounded budget, marks libraries a server does not carry as `absent` once per library rather than pending forever, reports a job whose per-server steps failed as finished with warnings, and adds a catch-up run per server: make server X match what the service already has, started by hand or automatically when a server appears or its library map changes.

Decisions the operator made during design: a catch-up delivers and writes what exists rather than re-rendering; libraries a server does not carry are recorded as `absent` and never retried; a catch-up starts automatically after a server is added or its map changes, as well as by hand; the mechanism extends the delivery model rather than adding a server-scoped full pass.

## 1. The outcome tables and the `absent` rule

Artwork keeps `render_deliveries` as it is, with one addition: an `attempts` counter. Metadata gets a sibling, `metadata_writes`: one row per item and server with `status` (`written`, `pending`, `failed`, `skipped`, `absent`), `detail` (the exemption reason or a failure class name, never a URL), `attempts`, `attempted_at`, `written_at`, `next_attempt_at`. The per-server write in `apply_metadata` records into it instead of only logging: a successful write is `written`; a refused or unreachable server is `pending` with a retry time; an exemption is `skipped` with its reason; a write turned off by `operations.write_to_<server>` is `skipped` with that reason, so the row still says why.

`absent` is decided once per library and server. A library is present on a server when its name, or its `library_map` partner, is among the folders that server lists; the service already reads that list to build the Jellyfin index and the Plex sections. An item whose library is absent on a server gets `absent` in both tables at once, is never resolved there, and is never retried; the item page shows one neutral line for that server. Presence is recomputed at the start of every full pass and every catch-up, so a library that appears later flips its items back to `pending` and they flow through the ordinary retry.

The first pass after this lands reclassifies existing `pending` artwork rows for libraries that are in fact absent, which clears the retry queue a mismatched map leaves behind today.

## 2. The pass and its server filter

`retry_pending_deliveries` becomes the retry pass for both tables. Its due query takes an optional `server` and an optional `run_id`; without either it behaves as today across all servers. A due artwork row is handled as now: resolve on that server, compose from the identity server when the bytes are needed, upload, record. A due metadata row resolves the item on that server, reads what the server holds, plans the edits against the current facts, writes, and records; a resolution miss stays `pending` on the same horizon, a write error becomes `pending` again until the budget runs out and then `failed` with its class name. Per-row isolation, one savepoint per row, covers both kinds.

The retry budget is explicit: a row is retried at the pass's cadence up to a bounded number of attempts (default 8, a `scheduler` setting), after which it is `failed` and stays visible until the next full pass or catch-up re-arms it. Nothing retries forever.

The pass reports per server (`jellyfin: 42 due, 40 written, 1 pending, 1 failed`), and the run history keeps that sentence so the dashboard can show it.

`process_item` gains no server filter. The normal pass keeps fanning out to every configured server, because that is what keeps the servers in step; scoping lives in the retry pass and in what a catch-up marks due.

## 3. The catch-up run and its triggers

A catch-up for server X is a run of its own kind, `catch_up`, with the server name on the run row. It starts three ways: the Catch up button on X's card (`POST /api/servers/{name}/catch-up`), the scheduled-runs entry the API exposes, and automatically after a restart that introduced X or after a save that changed X's `library_map` or `excluded_libraries`.

It is one pass over the database, not over the servers:

1. Recompute library presence for X from X's folder list; stamp `absent` where a library is not carried and `pending` where one has reappeared.
2. For every item in a present library: mark X's metadata row `pending` with `next_attempt_at` now; mark X's artwork row `pending` when it is missing, `failed`, or older than the render's current fingerprint; leave `uploaded` rows that match the fingerprint alone. Items never rendered are left to the full pass, the only thing that composes new artwork.
3. Refuse when X already has a catch-up in flight, and refuse when X's health poller reports it down, with a sentence the button shows.

The retry pass drains that backlog at its cadence, scoped by `run_id`, so the run row reports progress (rows due, done, failed) and finishes when nothing due remains. Because a catch-up over a large library is thousands of rows drained in batches of 500, the cadence is a per-run setting the button can shorten, defaulting to the scheduler's.

Cancelling a catch-up marks its remaining due rows back to their previous status; nothing already written is undone.

## 4. Job status, dashboard, item page

A `process_item` job ends `done` only when every server it touched recorded `written`, `uploaded`, `skipped` or `absent`. If any server's row ended `pending` or `failed`, the job ends in a new state, `done_with_warnings`, with `last_error` holding one sentence naming the servers and their outcomes (`jellyfin: metadata failed (HTTPStatusError 400)`). The queue treats it as finished: no retry of the whole job, since the per-server rows carry the retry. The dashboard counts the state as its own tile beside failed, and the action center lists those jobs with the sentence, so a systematic failure shows on the first pass as a growing count.

The item page's Deliveries block becomes a small per-server table: artwork status, metadata status, when, and the detail on hover, with `absent` rendered as one neutral line. The run history shows a catch-up as its own row with the per-server sentence.

## 5. API and the Servers tab

- `POST /api/servers/{name}/catch-up`: starts one; 409 while one is in flight or the server is down, with the sentence.
- `GET /api/servers/{name}/catch-up`: the current or last run's progress.
- `DELETE /api/servers/{name}/catch-up`: cancels the run in flight.
- `POST /api/servers/{name}/retry-failed`: re-arms X's `failed` rows in both tables without a full catch-up.
- `GET /api/items/{id}` carries both tables' rows per server.

The Servers tab's cards get Catch up and Retry failed, with a progress line while a run is in flight; the dashboard's scheduled-runs list shows the retry pass's per-server sentence; the runs list shows catch-ups by server.

## 6. Testing

Through the real entry points: a full pass on a dual registry with one library absent on Jellyfin records `absent` once per library and never resolves those items there; a metadata write that fails ends the job `done_with_warnings` with the sentence, is retried by the pass, and flips to `written` when the server recovers; the budget turns a persistently failing row `failed` and the next catch-up re-arms it; a catch-up marks exactly the rows the rule says (fresh metadata rows pending, only behind artwork rows pending, matching uploads untouched) and reports progress; cancelling restores prior statuses; the automatic trigger fires after a library-map change and after a restart that introduced a server, and not after an unrelated save; the item page serves both tables per server.

## 7. Amendments made during implementation

Three mechanisms this design named by effect and left open.

**"Older than the render's current fingerprint" (§3) is answered by a stored column.** `render_deliveries.fingerprint` holds the badge fingerprint each successful upload actually delivered, so a delivery is behind exactly when it is `IS DISTINCT FROM renders.badge_fingerprint`. Comparing timestamps was the alternative and it was rejected: `renders.updated_at` moves for reasons that have nothing to do with the badge, which would mark every matching upload due on every catch-up and lose §3's "matching uploads untouched". Rows uploaded before the column existed carry NULL, read as behind, and cost one redundant upload each on the first catch-up.

**"A restart that introduced X" (§3) is answered from the outcome tables.** A server named by the registry with no row in either table, on a database that already holds items, is one this deployment has just gained. No extra state is needed, it cannot fire twice — the catch-up's own marking writes those rows — and it is silent on a fresh deployment, whose first full pass covers every server anyway. It is filtered to the servers this deployment actually delivers something to, because a catch-up for a server with every toggle off writes no row at all and would therefore be queued again on every boot forever. The saved-change half hangs on the config swap rather than on a save route, which is what every path that replaces the running configuration already goes through, including the Settings design's when it exists; and it fires on one thing beyond the library map and the exclusions the design names — a `write_to_<server>` or `upload_to_<server>` toggle going from off to on, globally or for one library, since every row skipped while it was off is owed to that server the moment it is on and nothing else heals them before the next full pass. Both halves queue a server NAME and let the drain job turn it into a run: the boot hook and the config swap have no session of their own, and the drain is the first thing with one.

**A retried metadata write is planned from what the database holds, with no parental fetch.** The retry reads the item's stored `item_facts` row and its per-item overrides and passes `parental_categories=None` to the writer, so the parental labels are left to the full pass that owns the IMDb client rather than re-fetched per retried row. The alternative — re-gathering from the providers — would make the retry a second metadata pipeline with its own provider budget, for a pass whose whole purpose is to get what this service already decided onto a server that refused it. The consequence is that a row whose only missing field is a parental label settles as `written` and gains that label on the next full pass.
