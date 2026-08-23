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

/** GET /api/config returns the parsed config with secrets redacted. */
export type ConfigResponse = Record<string, unknown>;
