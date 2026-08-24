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

/** JOB_STATES in src/autoposter/api/routes.py, in the same order. */
export const JOB_STATES = [
  "pending",
  "running",
  "done",
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
}

export interface Status {
  jobs_by_state: Record<JobState, number>;
  workers: number;
  processed_last_24h: number;
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

/** GET /api/jobs/parked. `reason` is the job's last_error. */
export interface ParkedJob {
  id: number;
  kind: string;
  payload: Record<string, unknown> | null;
  attempts: number;
  reason: string | null;
  updated_at: string | null;
}

export interface ParkedJobsResponse {
  jobs: ParkedJob[];
}

/** GET /api/jobs -- the pending and running queue, for the Jobs overview.
 *
 * The raw payload is deliberately absent: the server lifts out the four fields
 * that name the item and keeps the rest (provider ids, source URLs, mode
 * filters) in the database.
 *
 * `max_attempts` is not a constant. A job waiting on Plex to index a new file
 * is retried against a much larger budget than any other failure, so the two
 * travel together and `attempts`/`max_attempts` is only meaningful as a pair.
 *
 * `run_in_seconds` is how long until the next attempt, and is negative for a
 * job that is already due -- every running job included.
 */
export interface QueuedJob {
  id: number;
  kind: string;
  state: string;
  attempts: number;
  max_attempts: number;
  waiting_for_plex: boolean;
  title: string | null;
  item_kind: string | null;
  season_number: number | null;
  episode_number: number | null;
  run_in_seconds: number;
  last_error: string | null;
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
 * other side null: `rating_key`/`plex_title`/`library` are null for an
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
  rating_key: string | null;
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
 * is the same idea from the other side -- arr entries with no path at all. */
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
  rating_key: string | null;
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
export interface ItemDetailResponse {
  id: number;
  title: string;
  library: string;
  kind: string;
  rating_key: string | null;
  facts: ItemFacts | null;
  renders: ItemRender[];
}

/** POST /api/items/{id}/reprocess. `queued` is false, with a null `job_id`,
 * when an identical job was already pending -- the endpoint de-duplicates on
 * the intent's dedupe_key rather than queueing a second one. */
export interface ReprocessResponse {
  queued: boolean;
  job_id: number | null;
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

/** GET /api/config returns the parsed config with secrets redacted, plus two
 * provenance keys that are not config fields: `overridden_paths` (dotted
 * paths carried by the overrides document) and `frozen_paths` (dotted-path
 * prefix -> why a change there needs a restart). `version` *is* a config
 * field. */
export type ConfigResponse = Record<string, unknown>;

/** The overrides document: only the fields the operator changed, nested the
 * way the config is. A key's absence is how "use the mounted file's value"
 * is expressed -- `null` is a value the API rejects. */
export type OverridesDocument = Record<string, unknown>;

/** PUT /api/config/overrides. `restart_required` lists the dotted paths the
 * running process cannot pick up without one. `inert` lists paths a restart
 * cannot pick up *either* -- currently just `api_docs_enabled`, which FastAPI
 * bakes into the application object before any override is read, so only
 * editing the mounted config file moves it. Kept out of `restart_required`
 * so that list stays a promise the page can keep. Optional/absent reads as
 * empty, so a response from before this field existed still renders. */
export interface ConfigSaveResponse {
  version_before: string;
  version_after: string;
  restart_required: string[];
  inert?: string[];
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
 * `by_art_kind` is not evidence that an edit picked those kinds. `config.version`
 * hashes the whole artwork section, so any artwork edit invalidates every
 * fingerprinted row and the breakdown is simply the shape of the library. Only
 * a gate (a disabled kind, `skip_tba`) can make the kinds differ.
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

/** `GET /api/version` -- what this pod is running, and whether Harbor has newer.
 *
 * `update_available` is a tri-state, and the null is the point: it means the
 * registry was not asked (no `version_check.harbor_url`, no robot token) or
 * could not be reached. That is not the same as "you are up to date", so the
 * sidebar shows no marker at all rather than one it cannot stand behind.
 * `latest` is null in exactly the same cases.
 *
 * The Harbor URL is deliberately absent from this shape: it is operator config
 * and never leaves the server -- see src/autoposter/api/version.py.
 */
export interface VersionResponse {
  version: string;
  latest: string | null;
  update_available: boolean | null;
}
