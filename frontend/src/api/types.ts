/** Shapes returned by the phase 4a API.
 *
 * Hand-written rather than generated: the endpoints return plain dicts built
 * in the route handlers, so there is no schema to generate from, and a
 * handful of interfaces do not justify a codegen step in the build.
 *
 * These were transcribed from the handlers in src/autoposter/api/routes.py,
 * not inferred from the field names a UI would find convenient -- a first
 * draft of this file guessed `member_count` on a collection and `id` on an
 * event, and neither exists.
 */

/** JOB_STATES in src/autoposter/api/snapshots.py, in the same order.
 *
 * `deferred` is a wait, not a failure: Plex cannot see the item yet, so the
 * queue holds the job on an unbounded horizon and claims it again when the
 * library catches up. It gets its own tile because it is neither pending work
 * nor a parked one. */
export const JOB_STATES = [
  "pending",
  "running",
  "deferred",
  "done",
  "done_with_warnings",
  "failed",
  "parked",
  "dismissed",
] as const;

export type JobState = (typeof JOB_STATES)[number];

export interface ScheduledRun {
  name: string;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_status: string | null;
  last_detail: string | null;
  /** The job's cadence, merged in from the running scheduler rather than read
   * from the database. Null when this deployment did not register the job --
   * registration is config-conditional -- so the next run is unknowable and
   * must not be computed. The server sends no next-run time: the client adds
   * this to `last_started_at` itself. */
  interval_seconds: number | null;
  /** Derived read-time on the server (api/snapshots.py's `_run_status`), not
   * stored: `"running"` or `"interrupted"` while a claimed run has no later
   * finish, distinguished by comparing its start against the server's own
   * boot instant; otherwise the recorded `last_status`, including null for a
   * job that has never run. The server only ever writes "ok" or "failed" to
   * `last_status` (scheduler/core.py's `_maybe_run`), so this union is
   * closed rather than widened with `| string` -- a real discriminated union
   * so the pill branches that switch on it typecheck. */
  status: "running" | "interrupted" | "ok" | "failed" | null;
}

export interface Status {
  jobs_by_state: Record<JobState, number>;
  workers: number;
  processed_last_24h: number;
  /** Which media servers this deployment's config configures (spec §9) --
   * not which ones this process actually connected to. The sidebar hides a
   * Plex-only entry when `plex` is false. */
  capabilities: { plex: boolean; jellyfin: boolean };
  scheduled_jobs: ScheduledRun[];
}

/** GET /api/events. Note there is no id and no free-text detail. */
export interface EventEntry {
  source: string;
  event_type: string;
  outcome: string;
  received_at: string | null;
}

export interface EventsResponse {
  events: EventEntry[];
}

/** One line of GET /api/dashboard/stream: exactly what GET /api/status and
 * GET /api/events?limit=25 return, in one object, so the dashboard renders a
 * pushed snapshot with the same code that rendered the fetched pair. The
 * stream also carries `{"heartbeat": true}` keepalives, which carry neither
 * field -- see isDashboardSnapshot. */
export interface DashboardSnapshot {
  status: Status;
  events: EventEntry[];
}

export function isDashboardSnapshot(value: unknown): value is DashboardSnapshot {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as DashboardSnapshot).status === "object" &&
    (value as DashboardSnapshot).status !== null &&
    Array.isArray((value as DashboardSnapshot).events)
  );
}

/** GET /api/jobs/parked. `reason` is the job's last_error.
 *
 * The raw payload is deliberately absent, matching GET /api/jobs: the server
 * lifts out the four fields that name the item (api/jobs.py's precedent) and
 * keeps the rest -- provider ids, source URLs -- in the database. */
export interface ParkedJob {
  id: number;
  kind: string;
  attempts: number;
  reason: string | null;
  updated_at: string | null;
  title: string | null;
  item_kind: string | null;
  season_number: number | null;
  episode_number: number | null;
}

export interface ParkedJobsResponse {
  jobs: ParkedJob[];
}

/** GET /api/actions/job-warnings -- jobs that finished with warnings, newest
 * first, for the Action Center's own panel.
 *
 * The rows are `ParkedJob`s and the type is reused rather than copied: the
 * endpoint lifts the same four naming fields out of the payload that
 * `/api/jobs/parked` does, from the same helpers, so a second interface would
 * only be able to drift from this one. `total` is the count across the whole
 * state, not the length of this page.
 *
 * `reason` here is the job's last error, which for this state names the
 * servers the job still owes rather than a failure that stopped it. */
export interface JobWarningsResponse {
  jobs: ParkedJob[];
  total: number;
}

/** GET /api/jobs -- the pending and running queue, for the Jobs overview.
 *
 * The raw payload is deliberately absent: the server lifts out the four fields
 * that name the item and keeps the rest (provider ids, source URLs, mode
 * filters) in the database.
 *
 * `max_attempts` is null for a deferred job and only for one: that job is
 * waiting on Plex to index the item, on a horizon with no end, so there is no
 * cap to count it against. `attempts`/`max_attempts` is only meaningful as a
 * pair, and a null half means "no deadline", not "unknown".
 *
 * `last_error` and `waiting_reason` are the same stored string sorted by which
 * one is true of the row: a deferred job has not failed, so it reports a
 * reason and no error, and every other row reports an error and no reason.
 * At most one of them is ever non-null.
 *
 * `run_in_seconds` is how long until the next attempt, and is negative for a
 * job that is already due -- every running job included.
 */
export interface QueuedJob {
  id: number;
  kind: string;
  state: string;
  attempts: number;
  max_attempts: number | null;
  waiting_for_plex: boolean;
  title: string | null;
  item_kind: string | null;
  season_number: number | null;
  episode_number: number | null;
  run_in_seconds: number;
  last_error: string | null;
  waiting_reason: string | null;
  created_at: string | null;
  /** Set once a running job's cancel has been requested, and persists across
   * reloads -- unlike the client-only note the cancel click shows, this is
   * read from the row itself, so an operator who reloads mid-attempt still
   * sees the pending cancel instead of losing that state. */
  cancel_requested: boolean;
}

export interface QueuedJobsResponse {
  jobs: QueuedJob[];
  /** How many jobs are live in total; the list itself is capped server-side. */
  total: number;
}

/** POST /api/jobs/{id}/cancel. Exactly one of the two flags is present:
 * `cancelled` for a pending job that was stopped outright, `cancel_requested`
 * for a running one the worker will drop when its attempt ends. */
export interface CancelJobResponse {
  id: number;
  state: string;
  cancelled?: boolean;
  cancel_requested?: boolean;
  detail?: string;
}

/** One row of GET /api/id-mismatches.
 *
 * The same shape carries all three groups, so the unmatched ones leave the
 * other side empty: `refs`/`plex_title`/`library` are empty/null for an
 * `arr_only` row, `arr_title` is null for a `plex_only` one, and that absence
 * is the finding rather than missing data.
 *
 * `arr_ids`/`plex_ids` are keyed by agent (`tmdb`, `tvdb`, `imdb`) and hold
 * only the ids that side actually has. `differing` names the agents both sides
 * hold and disagree about -- an id only one side carries is not a
 * disagreement, so it is never listed there. In `mismatched`, `differing` can
 * also carry `"no_ids_on_plex"` or `"no_ids_on_arr"`: a path-matched pair
 * where one side has no comparable ids at all, which is not a per-agent
 * disagreement but is exactly the mismatch this view exists to surface.
 */
export interface IdMismatchRow {
  service: string;
  kind: string;
  path: string;
  library: string | null;
  refs: Record<string, string>;
  plex_title: string | null;
  arr_title: string | null;
  year: number | null;
  arr_ids: Record<string, string>;
  plex_ids: Record<string, string>;
  differing: string[];
}

/** GET /api/id-mismatches.
 *
 * The three lists are capped at `limit` rows in total while `counts` always
 * reports everything found, so a remount that moved every path shows its true
 * size without serialising the whole library. `skipped` names the services
 * that were not asked at all (disabled, or without a base URL or api key);
 * `refused` names a service whose own root folders share no tree with the
 * configured arr path -- the wrong instance or a bad base URL -- mapped to a
 * message naming why, with that service excluded from every group rather than
 * reporting its whole library as arr_only and plex_only at once. `unmapped`
 * counts Plex items outside the configured root, which the service does not
 * manage and whose absence there is therefore not a finding; `arr_unmapped`
 * is the same idea from the other side -- arr entries with no path at all.
 * `arr_unreleased` counts arr entries the service manages but has not
 * downloaded yet (an upcoming movie, an unaired season) -- Plex cannot
 * possibly have matched those either, so they are not `arr_only` findings,
 * just a count. `excluded_libraries` echoes `config.plex.excluded_libraries`
 * -- those libraries are never walked, so their absence from `plex_only` is
 * deliberate rather than a sign everything there is registered. */
export interface IdMismatchesResponse {
  mismatched: IdMismatchRow[];
  arr_only: IdMismatchRow[];
  plex_only: IdMismatchRow[];
  counts: Record<string, number>;
  total: number;
  limit: number;
  skipped: string[];
  refused: Record<string, string>;
  unmapped: number;
  arr_unmapped: number;
  arr_unreleased: number;
  excluded_libraries: string[];
}

/** GET /api/collections.
 *
 * The four reconcile stats are nullable and null on every row no pass has
 * stamped -- which is every row predating the migration that added them, and
 * every smart row permanently: Plex evaluates a smart collection's filter
 * live, so there is no member count for us to have. Null and 0 are different
 * states. `last_added: 0` means a pass ran and found nothing to change;
 * `last_added: null` means no pass has reported. Render them differently.
 */
export interface CollectionSummary {
  id: number;
  library: string;
  title: string;
  kind: string;
  member_count: number | null;
  last_added: number | null;
  last_removed: number | null;
  last_reconciled_at: string | null;
}

export interface CollectionsResponse {
  collections: CollectionSummary[];
}

export interface ItemSummary {
  id: number;
  title: string;
  library: string;
  kind: string;
  refs: Record<string, string>;
  /** art_kind -> render status. */
  render_status: Record<string, string>;
}

export interface ItemsResponse {
  total: number;
  items: ItemSummary[];
}

/** GET /api/items/filters. Distinct values, each already sorted by the server,
 * so the library browser's controls offer values that exist across the whole
 * library rather than only those on the page being shown. */
export interface ItemFiltersResponse {
  libraries: string[];
  kinds: string[];
  statuses: string[];
}

/** One server's outcome for one render (`render_deliveries`), as nested under
 * each row of `ItemRender.deliveries`. `status` is `uploaded` / `pending` /
 * `failed` / `skipped`. */
export interface ItemRenderDelivery {
  server: string;
  status: string;
  attempted_at: string | null;
  uploaded_at: string | null;
  next_attempt_at: string | null;
  detail: string | null;
}

/** One server's `metadata_writes` row, as nested under each entry of
 * `ItemDetailResponse.servers`. `status` is `pending` / `failed` / `written` /
 * `skipped` / `absent`; `absent` is a statement about the LIBRARY -- this
 * server does not carry the item's library -- and not about this write. */
export interface ItemMetadataWrite {
  status: string;
  detail: string | null;
  attempts: number;
  attempted_at: string | null;
  written_at: string | null;
  next_attempt_at: string | null;
}

/** One server's delivery of one art kind, as nested under each entry of
 * `ItemDetailResponse.servers`. `status` is `pending` / `failed` /
 * `uploaded` / `skipped` / `absent`, with `absent` carrying the same
 * library-level meaning it does on a metadata write.
 *
 * The same underlying `render_deliveries` row `ItemRender.deliveries` carries,
 * pivoted the other way: there it hangs off the render and names the server,
 * here it hangs off the server and names the art kind. */
export interface ItemServerArtwork {
  art_kind: string;
  status: string;
  detail: string | null;
  attempts: number;
  attempted_at: string | null;
  uploaded_at: string | null;
  next_attempt_at: string | null;
}

/** One configured server's whole outcome for one item: what was written to it
 * and what was uploaded to it. `metadata` is null for a server with no
 * `metadata_writes` row yet, and `artwork` is empty for one with no delivery
 * rows -- neither is an error, only work that has not happened.
 *
 * Servers are ordered by name and each server's `artwork` by art kind, both
 * server-side, so the table does not reorder between page loads. */
export interface ItemServerOutcome {
  server: string;
  metadata: ItemMetadataWrite | null;
  artwork: ItemServerArtwork[];
}

/** One row of `renders`, as GET /api/items/{id} returns it. `(item_id,
 * art_kind)` is unique, so `art_kind` identifies a row within an item. */
export interface ItemRender {
  art_kind: string;
  status: string;
  fingerprint: string | null;
  badge_fingerprint: string | null;
  upload_status: string;
  adopted: boolean;
  rendered_at: string | null;
  uploaded_at: string | null;
  /** Which source supplied the artwork. `"manual"` is the database's only
   * trace of a manual override file, so it is what the clear-override control
   * keys off. Null on a row nothing has rendered yet. */
  provider: string | null;
  /** The provider URL the base image came from, so the candidate browser can
   * mark which of that provider's many images is the one in use. Under a
   * manual override this is the override file's own path, not a URL. */
  source_url: string | null;
  /** Whether the base image carried no burned-in text. Null under a manual
   * override, where there was no candidate to ask. */
  textless: boolean | null;
  /** Per-server delivery outcome, one entry per server this render has ever
   * been owed to. */
  deliveries: ItemRenderDelivery[];
}

/** One artwork option from one provider, as GET
 * /api/items/{id}/candidates/{art_kind} returns it.
 *
 * `thumb_url` is a grid-sized variant where the provider serves one (TMDB does,
 * by URL prefix) and the image itself otherwise. Both URLs are public and
 * unauthenticated, unlike this project's own image endpoints -- they are the
 * one case where a plain `<img src>` is correct instead of `apiFetchImage`.
 *
 * `includes_text` is TVDB's explicit flag and null everywhere else; `language`
 * is null for an image the provider tagged with no language at all. */
export interface ArtCandidate {
  provider: string;
  url: string;
  thumb_url: string;
  language: string | null;
  width: number | null;
  height: number | null;
  score: number;
  includes_text: boolean | null;
}

/** GET /api/items/{id}/candidates/{art_kind}.
 *
 * Ordered by the same ranking the render path uses, with candidates in no
 * configured language last rather than dropped -- the ladder discards those,
 * a browser must still offer them.
 *
 * `errors` carries one entry per provider that failed, keyed by provider name;
 * the value is the exception's type name, deliberately not its message. A
 * failing provider costs its own rows only, so a non-empty `errors` with a
 * non-empty `candidates` is the normal partial result, not an error state.
 *
 * `current` is the render row's base image, for marking the in-use candidate.
 * Always null for `logo`, which has no render row of its own. */
export interface CandidatesResponse {
  candidates: ArtCandidate[];
  errors: Record<string, string>;
  current: { source_url: string | null; provider: string | null } | null;
}

/** POST /api/items/{id}/candidates/{art_kind}/pick.
 *
 * `status` is always "picked" -- a failure raises rather than reporting itself
 * in the body. `queued` is whether a re-render was enqueued, which the
 * reprocess de-duplication can make false.
 *
 * The pick OVERWRITES any existing manual override file and keeps no backup:
 * the mount is the operator's own, not this service's to version. Say so
 * before the click, not after. */
export interface PickResponse {
  status: string;
  queued: boolean;
}

/** POST /api/items/{id}/renders/{art_kind}/manual. An operator-supplied source
 * (a URL, or a path under the manual-assets mount) installed as this item's base
 * artwork. `status` is always "installed" -- a refusal raises rather than
 * reporting itself in the body. `queued` is whether a re-render was enqueued,
 * which the reprocess de-duplication can make false. */
export interface ManualInstallResponse {
  status: string;
  queued: boolean;
}

/** POST /api/collections/{id}/poster. The endpoint writes the file and stops:
 * a collection poster is applied by the reconciler, so `applies` is
 * "next reconcile" and Diff now is how an operator makes that immediate. */
export interface CollectionPosterResponse {
  status: string;
  applies: string;
}

/** POST /api/collections/preview (src/autoposter/api/collections_builders.py).
 * Both filters are optional; omitting both previews every definition in every
 * configured library and additionally runs the delete sweep. Passing `title`
 * (a per-definition preview) skips the sweep -- the server runs it only for
 * an unfiltered library, so a filtered preview never reports a deletion. */
export interface CollectionPreviewRequest {
  library?: string;
  title?: string;
}

/** One row of a preview's `definitions`: a config definition's dry-run
 * outcome, a library that could not be previewed (`title: "(library)"`), or a
 * delete-sweep candidate (`deleting` set, `title` the collection's own).
 * `adding`/`removing` are computed against the collection Plex already has
 * *before* the ownership/collision check that decides whether anything would
 * really be touched -- so they can overstate next to an action string that
 * says the collection would be left untouched (a title collision, or a
 * protected label). The action strings are the authority; the counts are not. */
export interface DefinitionPreviewResult {
  title: string;
  library: string;
  adding: number;
  removing: number;
  deleting: number;
  unresolved: number;
  failed: boolean;
  skipped: boolean;
  actions: string[];
}

export interface CollectionPreviewResponse {
  definitions: DefinitionPreviewResult[];
  actions: string[];
}

/** One operator-configured definition as GET /api/collections/definitions
 * serves it: the config-side facts only — no counts, no Plex state (that is
 * the preview's job). `libraries: null` means "every configured library".
 * `provenance` says which layer supplies the entry: "file" rows belong to
 * the mounted YAML and are never written into the overrides document (the
 * freezing hazard `api/overrides.ts` opens with); "override" rows are the
 * stored overrides list's own and are the only removable ones.
 *
 * A LOSSY projection of `CollectionDefinition` (config/schema.py), which also
 * carries `summary`, `limit`, `schedule`, `labels` and more. These seven
 * fields are what a listing DISPLAYS; nothing that writes the overrides
 * document may be built from them, or a stored definition's unprojected
 * fields would be dropped by the next save. */
export interface DefinitionSummary {
  title: string;
  builder: string;
  params: Record<string, unknown>;
  libraries: string[] | null;
  sort: string;
  sync_mode: string;
  provenance: "file" | "override";
}

/** GET /api/collections/definitions — row 137's listing. `libraries` is
 * `collections.libraries`, the names the create form offers as scope. */
export interface DefinitionsListingResponse {
  libraries: string[];
  definitions: DefinitionSummary[];
}

/** One row of GET /api/playlists/definitions.
 *
 * Not `DefinitionSummary`: a playlist has no `sort`, and it has three fields
 * the collections listing withholds (`summary`, `limit`, `schedule` — the 98a
 * branch review's L-7, fixed in 98b because the editor cannot show what the
 * listing does not carry).
 *
 * `schedule` stays `Record<string, unknown> | null` rather than naming
 * `ScheduleGate`'s two fields, deliberately: the server sends the whole model
 * (`every_n_runs` and `months`), and nothing on this side reads either one —
 * the form does not show a schedule and the write is seeded from the stored
 * document, never from this row. A named type here would be a second place to
 * keep in step with `config/schema.py` for no reader.
 *
 * `provenance` has a third value here. A `"preset"` row is one
 * `playlists.presets` expands into: it is stored in no document, so it is
 * neither editable nor removable through the overrides layer — switching it
 * off means removing its `preset_key` from `playlists.presets`, which the
 * settings page's string-list editor already does. `preset_key` is null on
 * every other row. */
export interface PlaylistDefinitionSummary {
  title: string;
  builder: string;
  params: Record<string, unknown>;
  libraries: string[] | null;
  summary: string | null;
  limit: number | null;
  schedule: Record<string, unknown> | null;
  sync_mode: string;
  builder_level: string;
  provenance: "file" | "override" | "preset";
  preset_key: string | null;
}

/** A switched-on preset an operator definition of the same title displaced.
 * The preset is not built and is not listed as a row; it is named here so the
 * panel can say which key stopped building rather than leaving the operator
 * to notice a playlist quietly changing shape. */
export interface PlaylistPresetConflict {
  key: string;
  title: string;
}

/** GET /api/playlists/definitions. `libraries` is the playlists section's own
 * scope, in the order a definition searches it. */
export interface PlaylistDefinitionsListingResponse {
  libraries: string[];
  definitions: PlaylistDefinitionSummary[];
  preset_conflicts: PlaylistPresetConflict[];
}

/** POST /api/collections/parse-source — a pasted URL resolved to the builder
 * and params the definition will carry. Shape-checked only: existence is the
 * first pass's business. A refusal is a 422 whose detail is one sentence. */
export interface ParsedSourceResponse {
  builder: string;
  params: Record<string, unknown>;
  display_note: string;
}

/** One row of the preset catalog, as `catalog_listing`
 * (src/autoposter/collections/catalog.py) serves it.
 *
 * `setting` is the field that decides what a row's checkbox *writes*, and the
 * two answers are not variations of one another:
 *
 *   - `null` -- an ordinary preset. Its `active` state is membership of
 *     `collections.presets`, and switching it on appends its `key` there.
 *   - a dotted config path (`"collections.awards"`) -- a row that renders a
 *     boolean setting that shipped before the catalog existed. Its `active`
 *     state is that boolean's value, and switching it on writes that path.
 *     Its key is deliberately NOT a preset key: the server refuses
 *     `presets: [oscars]` as unknown, so a picker that wrote one there would
 *     produce a config the API rejects.
 *
 * `readiness` is `"ready"` or `"gated"`; a gated row names the roadmap row it
 * waits on in `gated_row`, and the config refuses its key outright, so the
 * picker renders it disabled rather than offering a switch that cannot be
 * thrown. */
export interface CatalogPreset {
  key: string;
  name: string;
  /** The static collection titles this preset builds. Empty for a preset
   * whose collections are all dynamic. */
  titles: string[];
  /** The whole clause describing this preset's dynamic family ('one per
   * ceremony, named "Cannes <year>"'), or null where the preset builds none.
   * Not a title that will exist -- which years the dataset carries, or which
   * values the library holds, is not knowable here. */
  years_title: string | null;
  description: string;
  /** The Kometa defaults file this reproduces, verbatim, or an honest
   * statement that it has none. */
  kometa_source: string;
  library_types: string[];
  readiness: string;
  gated_row: number | null;
  setting: string | null;
  active: boolean;
}

export interface CatalogCategory {
  key: string;
  label: string;
  /** Empty is an ordinary state: every category is listed whether or not its
   * rows have been written, so a category cannot silently vanish from the
   * picker. */
  presets: CatalogPreset[];
}

/** One collection group as the catalog endpoint enumerates it: the
 * config-legal key, the divider's display title, the `!NNN` section number
 * the RUNNING config gives it, and its index under that config's
 * `collections.group_order`. Served in effective order — `position` equals
 * the array index — so the Groups panel renders the array and never holds
 * its own copy of the keys. */
export interface CatalogGroup {
  key: string;
  title: string;
  section: string;
  position: number;
}

/** GET /api/collections/catalog. Touches neither Plex nor the database -- it
 * is a pure table plus which keys the live config has switched on, and the
 * group enumeration in the running config's effective order. */
export interface CollectionsCatalogResponse {
  categories: CatalogCategory[];
  groups: CatalogGroup[];
  /** The 22 separator colour styles, served so no name lives client-side. */
  separator_styles: string[];
  /** The RUNNING config's collections.separator_style. */
  separator_style: string;
}

/** The JSON outcome of POST /api/testing/sample when the title does not fit at
 * the minimum point size. The pipeline writes no artifact in that case, so the
 * endpoint returns this instead of image bytes -- a labelled outcome, not an
 * error. */
export interface SampleTruncatedResponse {
  truncated: boolean;
  art_kind: string;
  length: string;
}

/** The facts the badges are drawn from. `originally_available` is a date, not
 * a timestamp -- it serialises as "2019-06-28" with no time in it. */
export interface ItemFacts {
  critic_rating: number | null;
  audience_rating: number | null;
  content_rating: string | null;
  genres: string[];
  studio: string | null;
  originally_available: string | null;
}

/** GET /api/items/{id}. `facts` is null for an item nothing has been collected
 * for yet, which is an ordinary state, not an error. */
/** The show an episode or season belongs to. Null when the item has no
 * parent (a movie or a show itself) or the parent has not been processed
 * yet -- the backend's upsert leaves `parent_id` null in that case rather
 * than inventing a name (src/autoposter/render/pipeline.py's
 * `_upsert_media_item`), and the response degrades the same honest way. */
export interface ItemParent {
  id: number;
  title: string;
}

export interface ItemDetailResponse {
  id: number;
  title: string;
  library: string;
  kind: string;
  refs: Record<string, string>;
  /** From the item's own row. Null for a movie or show; a season carries
   * only `season_number`, an episode carries both. */
  season_number: number | null;
  episode_number: number | null;
  parent: ItemParent | null;
  facts: ItemFacts | null;
  /** One entry per server this item has any outcome row for, metadata and
   * artwork together. Empty until the item has been processed once. */
  servers: ItemServerOutcome[];
  renders: ItemRender[];
}

/** POST /api/items/{id}/reprocess. `queued` is false, with a null `job_id`,
 * when an identical job was already pending -- the endpoint de-duplicates on
 * the intent's dedupe_key rather than queueing a second one.
 *
 * `note` is a fixed sentence the server sends when another `media_items` row
 * carries this item's identity under a different Plex rating key -- the one
 * condition under which the render pipeline's fork stop can complete the job
 * having changed nothing. It is always present and null when there is no such
 * row, never absent. Render it verbatim: the server owns the words, and it is
 * deliberately a "may" rather than a "will", because the twin's existence is
 * necessary for that stop and not sufficient. */
export interface ReprocessResponse {
  queued: boolean;
  job_id: number | null;
  note: string | null;
}

/** POST /api/items/{id}/renders/{art_kind}/clear-override.
 *
 * `status` is always "cleared" -- the endpoint raises rather than reporting a
 * failure in the body. `queued` is whether a re-render was enqueued, which the
 * reprocess de-duplication can make false.
 *
 * The override file is RENAMED to `<name>.disabled`, not deleted: it is the
 * operator's own artwork and restoring it is a rename back on the mount. Say
 * so; "deleted" would be false. The endpoint is also not idempotent -- a
 * second call 409s, because the file it would move is no longer there.
 */
export interface ClearOverrideResponse {
  status: string;
  queued: boolean;
}

/** POST /api/full-pass. `queued` is how many jobs were actually inserted;
 * `skipped` counts items whose identical job was already pending, so a second
 * trigger during a pending pass reports mostly-skipped rather than pretending
 * to queue the library again. */
export interface FullPassResponse {
  total: number;
  queued: number;
  skipped: number;
}

/** One line of GET /api/logs and /api/logs/stream. The stream also carries
 * `{"heartbeat": true}` keepalives, which are not log lines and carry none of
 * these fields -- see isLogLine. */
export interface LogLine {
  ts: string;
  level: string;
  logger: string;
  message: string;
}

export function isLogLine(value: unknown): value is LogLine {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as LogLine).message === "string" &&
    typeof (value as LogLine).level === "string"
  );
}

/** POST /api/scheduled-runs/{name}/run. "requested", never "started": the
 * endpoint only marks the job due, so the run begins on one of the
 * scheduler's next polls. `poll_seconds` is that poll interval, which is what
 * the UI should quote as the upper bound on the wait. */
export interface ScheduledRunRequestResponse {
  status: string;
  poll_seconds: number;
}

export interface LoginResponse {
  token: string;
  expires_at: string;
}

/** GET /api/config returns the parsed config with secrets redacted, plus the
 * keys that are not config fields: `frozen_paths` (dotted-path prefix -> why a
 * change there needs a restart), `restart_paths` (the settings a save changed
 * that the running process has not picked up) and the rest of the list
 * `api/overrides.ts::PROVENANCE_KEYS` names. `version` *is* a config field. */
export type ConfigResponse = Record<string, unknown>;

/** The overrides document: the whole configuration, nested the way the config
 * is, which is what the store holds. A key's absence is how "use the schema's
 * own default" is expressed -- `null` is a value the API rejects. */
export type OverridesDocument = Record<string, unknown>;

/** PUT /api/config/overrides. `restart_required` lists the dotted paths the
 * running process cannot pick up without one. `inert` lists paths no swap
 * reaches even in part -- currently just `api_docs_enabled`, which FastAPI
 * bakes into the application object before any override is read, so only a
 * restart moves it, and the restart reads the stored document rather than
 * the merged overrides. Kept out of `restart_required` so that list stays a
 * promise the page can keep. Optional/absent reads as
 * empty, so a response from before this field existed still renders. */
export interface ConfigSaveResponse {
  version_before: string;
  version_after: string;
  restart_required: string[];
  inert?: string[];
  /** The revision of the document this write stored.
   *
   * Served for a future consumer and for cross-checking; no page reads it
   * today. All four re-seed from the follow-up `GET /api/config` instead,
   * which is the same token by construction (the backend pins that the two
   * agree) and also refreshes everything else the page renders. A page that
   * wanted to skip that round trip could take the revision from here.
   * Optional so a response from before the field existed still parses. */
  overrides_revision?: string;
}

/** The rows a candidate config would invalidate, as
 * `src/autoposter/config/impact.py` counts them.
 *
 * `of_total` is the population the walk *examined*, not the renders table: a
 * row for an art kind the candidate disables, a title card its skip rules
 * skip, and a row with no stored fingerprint are excluded from both numbers.
 * `affected` is an over-estimate by construction -- see that module's
 * docstring -- so it renders with a "~".
 *
 * `by_art_kind` is evidence about the kinds whose stored fingerprints an
 * edit moves -- not, without qualification, which kinds it touched. The
 * render version is computed per art kind (`config/loader.py`'s
 * `render_version_for`), so an edit to one kind's settings invalidates that
 * kind's rows and leaves the others alone. A shared input -- an asset root,
 * `library_folders`, `artwork.use_original_title`, the global
 * `artwork.disable_online_asset_fetch`, `artwork.output_quality` -- is a
 * member of every kind's payload and still reaches every row; that is the
 * edit being global, not the breakdown failing to discriminate. A gate (a
 * disabled kind, `skip_tba`) can also narrow it. And a poster with a
 * composited logo is counted for any render-affecting edit, because
 * `affected`'s approximation means the walk cannot see the logo it was
 * built with, whichever kind moved.
 */
export interface ConfigImpact {
  affected: number;
  by_art_kind: Record<string, number>;
  of_total: number;
}

/** POST /api/config/preview. Persists nothing, queues nothing, swaps nothing.
 * `impact` is null when the edit cannot change a rendered image -- which is
 * not "zero items", it is "the question does not apply". */
export interface ConfigPreviewResponse extends ConfigSaveResponse {
  impact: ConfigImpact | null;
  /** How many managed collection posters a `collections.poster_title` edit
   * would re-composite and re-upload, once. Separate from `impact` because
   * `config/impact.py` walks the `renders` table and a collection has no row
   * there -- so this number is real precisely when `impact` is null. Optional
   * because an older server does not send it, and an OVER-estimate when it
   * does (a poster the operator placed themselves passes through untouched),
   * so render it with a "~". */
  collection_posters?: number;
}

/** One row of GET /api/config/snapshots -- metadata only. The documents are
 * not in the listing on purpose: the list needs to label its rows, and one of
 * those documents holds the notification URL. */
export interface ConfigSnapshot {
  id: number;
  created_at: string;
  path_count: number;
  reason: string;
}

/** GET /api/config/snapshots/{id}. `document` is redacted exactly as
 * `GET /api/config` redacts the live one. */
export interface ConfigSnapshotDetail extends ConfigSnapshot {
  document: OverridesDocument;
}

/** GET /api/config/overrides/export. `document` is UNREDACTED -- a redacted
 * backup would write the bare notification host back over the real URL on the
 * next import. The download copy tells the operator so. */
export interface ConfigExport {
  autoposter_overrides: number;
  exported_at: string;
  document: OverridesDocument;
}

/** POST /api/config/apply: the PUT's own response plus what it enqueued.
 * `skipped` counts items whose identical job was already pending -- the same
 * dedupe arbiter the full-pass button reports through, not a failure. */
export interface ConfigApplyResponse extends ConfigSaveResponse {
  queued: number;
  skipped: number;
}

/** POST /api/artwork-modes/{mode}. The filters and the apply switch every
 * Plex-writing mode takes (`ModeFilterBody` in src/autoposter/api/routes.py).
 *
 * Each field is omitted rather than sent empty: `apply` omitted would fall back
 * to that mode's configured default, and the page is explicit about which run
 * it is asking for, so it always sends it. The backup mode takes no body at
 * all.
 */
export interface ArtworkModeRequest {
  type?: string;
  library?: string;
  item_id?: number;
  apply: boolean;
}

/** What every artwork mode answers with.
 *
 * The six modes return the same envelope -- `mode`, `status`, `dry_run` and a
 * flat set of counts -- but *which* counts differ per mode and per outcome: a
 * dry run omits the outcome split (`pushed`/`failed`, `reset`/`failed`,
 * `uploaded`/`failed`) because nothing was attempted, and the backup mode has
 * no `dry_run` at all since it never writes to Plex.
 *
 * So the numbers are typed as an index signature rather than enumerated. That
 * is deliberate rather than lazy: a fixed list here would have to be kept in
 * step with five dataclasses, and a count this file forgot would silently stop
 * being displayed. The page renders whatever numbers arrive, under a label
 * table, so a new count shows up unlabelled rather than not at all.
 *
 * `status` also carries the refusals (`"refused"`, `"backup failed"`), which
 * are answers and not errors: they arrive as a 200 with real counts and a
 * `reason` naming the numbers. `note` is the reset mode's standing warning
 * that the artwork it replaced is still on the Plex server.
 */
export interface ArtworkModeResponse {
  mode: string;
  status: string;
  dry_run?: boolean;
  reason?: string;
  note?: string;
  [count: string]: string | number | boolean | undefined;
}

/** `GET /api/version` -- what this container is running, and whether a newer
 * release has been published.
 *
 * `update_available` is a tri-state, and the null is the point: it means the
 * question cannot be answered here, which is NOT the same as "you are up to
 * date". Three ways to get it:
 *
 * - This build is not a release. Only an image published by the release
 *   workflow carries a version the newest release tag can be compared with; a
 *   build of main reports `sha-<commit>`, which is neither ahead of a release
 *   nor behind one, so it never even asks.
 * - The background poll has not completed a successful pass yet, which
 *   includes the ordinary seconds between boot and the first poll, not only a
 *   GitHub that could not be reached.
 * - The newest published tag did not parse as a version.
 *
 * So the sidebar shows no marker at all rather than one it cannot stand
 * behind. `latest` is null in the first two cases -- `update_available` is
 * derived from it server-side, against this build's own stamp.
 *
 * No credential is involved anywhere in this: the releases endpoint is public
 * and is called anonymously -- see src/autoposter/api/version.py.
 */
export interface VersionResponse {
  version: string;
  latest: string | null;
  update_available: boolean | null;
}

/** `GET /api/facts/backfill` -- the one-shot facts backfill's standing progress.
 *
 * `complete` is derived server-side by the POST's own rule (`done >= total`),
 * which is why an empty library reads `complete` on both endpoints rather than
 * `not_started` here and `complete` there -- see
 * src/autoposter/api/facts_backfill.py. `not_started` means what it says: a
 * population that exists and has not been walked.
 */
export interface FactsBackfillStatus {
  status: "not_started" | "in_progress" | "complete";
  done: number;
  total: number;
}

/** `POST /api/facts/backfill` -- one triggered batch's outcome.
 *
 * `parked` (TMDb's shared 429 window is open) and `complete` are answers, not
 * failures: both arrive as a 200 with real counts and a `detail` that says
 * what happened. Only a non-2xx is an error.
 */
export interface FactsBackfillTrigger {
  status: "enqueued" | "parked" | "complete";
  enqueued: number;
  done: number;
  total: number;
  detail: string;
}

/** `GET /api/actions/summary` -- one entry per flag the server has, with its
 * count under the current library/art-kind scope.
 *
 * The label and description come from the server's own registry
 * (src/autoposter/actions/flags.py), not from a list here: a flag added there
 * would silently be missing from a chip row this file enumerated.
 *
 * `default_on` false means the flag is offered but is not part of the queue's
 * default population -- `unknown_provenance` is every row of a
 * wholesale-adopted library, and a queue that opens showing ten thousand rows
 * is not a queue. `instant` false means the flag cannot fire until a row
 * re-renders, because the fact it rests on is written at the render
 * write-back; the page says so on the chip.
 */
export interface ActionFlagSummary {
  code: string;
  label: string;
  description: string;
  default_on: boolean;
  instant: boolean;
  count: number;
}

export interface ActionsSummaryResponse {
  /** How many rows the default population holds -- not the sum of the counts
   * above, which double-count a row carrying two flags. */
  total: number;
  flags: ActionFlagSummary[];
}

/** One row of `GET /api/actions`: one chosen artwork asset, which is one
 * `renders` row.
 *
 * `flags` and `details` are parallel arrays in the same order -- the server
 * evaluates each flag's predicate and hands back the factual sentence behind
 * it, so the page renders the server's own words rather than restating a fact
 * it would have to keep in step.
 *
 * `evidence` is the SHA-256 the dismissal is keyed by. The page does not
 * compute or compare it; it is here because it is what makes a dismissal stop
 * holding when a fact moves, and a debugging operator should be able to see
 * it.
 */
export interface ActionRow {
  item_id: number;
  art_kind: string;
  title: string;
  library: string;
  kind: string;
  status: string;
  upload_status: string;
  provider: string | null;
  flags: string[];
  details: string[];
  dismissed: boolean;
  evidence: string;
  /** null means this row predates the quality taxonomy, so four of the flags
   * cannot be evaluated for it until it re-renders. */
  quality_scored_at: string | null;
  updated_at: string;
}

export interface ActionsResponse {
  total: number;
  limit: number;
  offset: number;
  items: ActionRow[];
}

/** `POST /api/actions/bulk/rerender` -- one batch's outcome, or a count of
 * what one would be.
 *
 * All three statuses arrive as a 200 with real numbers: "dry run" changed
 * nothing on purpose, and "complete" means nothing matches the filter. Only a
 * non-2xx is an error. `matched` counts flagged ROWS across the whole filter,
 * `items` counts the distinct items in THIS batch, and `enqueued` counts jobs
 * actually created -- a batch the pending dedupe swallowed reports fewer than
 * `items` rather than claiming work it did not queue.
 */
export interface BulkRerenderResponse {
  status: "dry run" | "enqueued" | "complete";
  matched: number;
  items: number;
  enqueued: number;
  detail: string;
}

/** `POST /api/actions/rebuild` -- roadmap row 233's Rebuild action.
 *
 * One endpoint, two request shapes: `{row: {item_id, art_kind}, apply: true}`
 * for the row button, or the same filter fields `BulkRerenderResponse`'s
 * request carries for the bulk bar. Both answer this.
 *
 * `matched` counts render ROWS across the whole request, `selected` counts
 * the rows in THIS batch (capped by `scheduler.drift_batch_size`), `cleared`
 * counts the fingerprints this press actually moved to NULL -- a second press
 * on the same row reports 0, because it is already cleared -- `items` counts
 * the distinct items behind those rows and `enqueued` counts jobs actually
 * created, which is fewer whenever the pending dedupe swallowed one.
 *
 * `items_detail` names the items this press queued, each with its `refs`
 * (every server's id for that item). It is the only non-numeric field, and it
 * is deliberate: row 213 permits counts and ids and nothing else, so there is
 * no `detail` sentence here -- the page writes its own copy from these
 * numbers. Named `items_detail` rather than `items` because `items` already
 * names the distinct-item count above.
 */
export interface RebuildResponse {
  status: "dry run" | "enqueued" | "complete";
  matched: number;
  selected: number;
  cleared: number;
  items: number;
  enqueued: number;
  items_detail: { id: number; refs: Record<string, string> }[];
}

/** `GET /api/actions/backfill` -- the quality backfill's standing progress.
 *
 * Measured over rows that CAN be scored (`status = 'rendered'`) rather than
 * over every render row: an asset that produced no art never reaches the
 * write-back that stamps the quality facts, so counting it would make a
 * progress bar that stops short forever. Those rows already have their own
 * flags.
 *
 * `complete` is derived server-side by the POST's own rule (`done >= total`),
 * so an empty library reads `complete` on both endpoints rather than
 * `not_started` here and `complete` there.
 *
 * `unscored_total` is `total - done`, the population a press can still draw
 * from. `queued_for_scoring` is how many of THAT population already have an
 * in-flight `process_item` job for their item -- pending, running or
 * deferred alike -- so an operator pacing a live run (the report that added
 * this pair: mid-run at 8214/17264, with no way to see the queue's actual
 * depth) can see how much is already claimed before pressing again.
 *
 * `blocked` is the further subset of the unscored population whose item
 * already has a `parked` `process_item` job -- a press cannot score these by
 * re-rendering; only an operator acting on the Failures page (fix the
 * source, retry, dismiss) can. It is excluded from `total`/`unscored_total`
 * for the reason `total` already excludes `no_art`/`skipped`/`truncated`/
 * `failed` rows: a row this button structurally cannot move must not sit in
 * its own denominator.
 */
export interface QualityBackfillStatus {
  status: "not_started" | "in_progress" | "complete";
  done: number;
  total: number;
  queued_for_scoring: number;
  unscored_total: number;
  blocked: number;
}

/** `POST /api/actions/backfill` -- one triggered batch's outcome.
 *
 * `complete` is an answer, not a failure: it arrives as a 200 with real
 * counts. `selected` is how many unscored assets this batch took;
 * `enqueued` is how many items it actually queued, which is smaller whenever
 * one item carries several unscored assets or a pass for it was already
 * pending.
 *
 * `queued_for_scoring` and `unscored_total` are read AFTER this press's own
 * enqueue, so they answer "what does the queue hold now", not what it held
 * before this batch -- and they are cumulative across presses, not this
 * batch's own size: a second press's `queued_for_scoring` covers this
 * batch's assets on top of whatever a still-in-flight earlier batch left
 * queued.
 */
export interface QualityBackfillTrigger {
  status: "enqueued" | "complete";
  selected: number;
  enqueued: number;
  done: number;
  total: number;
  queued_for_scoring: number;
  unscored_total: number;
  blocked: number;
  detail: string;
}

/** GET /api/items/{id}/metadata-overrides -- roadmap row 99.
 *
 * `writable` is a list of field NAMES and nothing else. It is deliberately
 * NOT a list of {field, current_value} pairs: serving what Plex or the facts
 * row currently holds would put today's values one Save away from being
 * frozen as overrides, which is the freezing hazard this document's own
 * `overrides.ts` describes ("Round-tripping the config would store today's
 * values as overrides, freezing them against every future change"). The panel
 * renders an empty input for a field with no override, and that emptiness is
 * the feature.
 *
 * `enabled` is `operations.item_overrides_enabled`, live. False does NOT hide
 * the panel: existing overrides are kept and ignored, and an operator whose
 * overrides silently stopped applying needs the panel to say so. */
export interface MetadataOverride {
  field: string;
  value: string;
  updated_at: string;
}

export interface MetadataOverridesResponse {
  enabled: boolean;
  kind: string;
  writable: string[];
  overrides: MetadataOverride[];
}

/** PUT and DELETE both answer this shape. `queued` is false when an identical
 * reprocess was already pending -- the endpoint deduplicates on the intent's
 * own key rather than queueing a second one. */
export interface MetadataOverrideWriteResponse {
  status: string;
  field: string;
  value?: string;
  unlocked?: boolean;
  /** DELETE only, and only when the item is exempt (roadmap row 35): the
   * Plex write was skipped rather than sent, fixed at `"skipped (exempt)"`. */
  plex?: string;
  queued: boolean;
}

/** The four art-kind counts on an attributed run. Always all four keys --
 * api/stats.py's always-all-keys rule -- so a chart never reads a field that
 * vanished because a pass composited no title cards. */
export interface RunRendered {
  poster: number;
  season_poster: number;
  background: number;
  title_card: number;
}

/** One row of GET /api/stats/runs.
 *
 * `rendered`, `processed`, `failed` and `deferred` are null **together**, and
 * null means "this run's window was not attributed" rather than "nothing
 * happened". Only a full pass is attributed: a scheduled job's window overlaps
 * whatever the worker pool was doing, so the server declines to claim that
 * work as the job's. Charts must skip a null rather than plot it as zero.
 *
 * `duration_seconds` is null while `finished_at` is null -- the run is still
 * open.
 *
 * `status` carries a fifth value alongside the four a full pass can reach:
 * `interrupted` is a scheduled run whose successor found it still open after
 * a restart (`scheduler/run_history.py`'s `open_run`) -- a full pass never
 * gets this value, but the union is shared because both kinds share the
 * column.
 *
 * There is deliberately no `detail` field: the server does not serve one
 * (roadmap row 213). */
export interface RunEntry {
  id: number;
  kind: "scheduled" | "full_pass";
  name: string;
  started_at: string;
  finished_at: string | null;
  status: "running" | "ok" | "failed" | "timed_out" | "interrupted";
  duration_seconds: number | null;
  rendered: RunRendered | null;
  processed: number | null;
  failed: number | null;
  deferred: number | null;
}

/** GET /api/stats/runs?limit=. Newest first. */
export interface RunsResponse {
  runs: RunEntry[];
  generated_at: string;
}

/** One service's re-registration result from the webhook secret rotation.
 *
 * The same three keys `api/setup.ts`'s `ArrRegistration` carries, and
 * deliberately declared again rather than imported: that module is the
 * WIZARD's client, with its own fetch and its own memory-only setup token,
 * and this file is the session API's vocabulary. A type import across that
 * line would be the first thread of a dependency neither side wants. */
export interface WebhookRotationResult {
  ok: boolean;
  /** `"created"` or `"updated"` on success, `null` otherwise. */
  action: string | null;
  /** A fixed sentence from the server, rendered verbatim -- never translated
   * from a status code. */
  detail: string;
}

export interface WebhookRotationResponse {
  /** The one and only serve of this value. There is no GET. */
  webhook_secret: string;
  /** The audit row's own timestamp, so the panel and the Dashboard's events
   * list cannot disagree. */
  rotated_at: string;
  /** Keyed by service name: `radarr`, `sonarr`. */
  registrations: Record<string, WebhookRotationResult>;
}

/** One file under `overlays_root` or `fonts_root` (roadmap row 55). A NAME,
 * never a path: the server serves basenames and the page never asks for more. */
export interface AssetFile {
  readonly name: string;
  readonly size: number;
  readonly modified: string;
  /** Ships with the service; the page offers no delete for it. */
  readonly protected: boolean;
  /** The config paths that currently name this file. Non-empty means the
   * server will refuse a delete, so the page says so before the click. */
  readonly referenced_by: readonly string[];
}

export interface AssetFilesResponse {
  readonly files: readonly AssetFile[];
}
