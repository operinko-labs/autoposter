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

/** GET /api/collections. Member counts are not exposed yet -- see phase 4c. */
export interface CollectionSummary {
  id: number;
  library: string;
  title: string;
  kind: string;
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

/** POST /api/full-pass. `queued` is how many jobs were actually inserted;
 * `skipped` counts items whose identical job was already pending, so a second
 * trigger during a pending pass reports mostly-skipped rather than pretending
 * to queue the library again. */
export interface FullPassResponse {
  total: number;
  queued: number;
  skipped: number;
}

export interface LoginResponse {
  token: string;
  expires_at: string;
}

/** GET /api/config returns the parsed config with secrets redacted. */
export type ConfigResponse = Record<string, unknown>;
