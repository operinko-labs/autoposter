/** Shared presentation rules for POST /api/scheduled-runs/{name}/run, used by
 * the dashboard's per-job Run now and the collections page's Diff now. Both
 * pages must say the same thing about the same endpoint, so the two strings
 * live here rather than being typed out twice. */

/** Why a run cannot be requested. Scheduler registration is
 * config-conditional, so a `ScheduledRun` row can outlive the job that wrote
 * it; `interval_seconds` is null exactly then. Marking such a row due seeds a
 * permanently-due row no scheduler will ever claim, so the control is
 * disabled and says why. */
export const NOT_SCHEDULED_TITLE = "not scheduled in this deployment's config";

/** What the endpoint actually promises. It marks the row due and returns; the
 * run begins on one of the scheduler's next polls, up to `pollSeconds` away.
 * Saying "running" here would be a claim the server never made -- and the
 * endpoint nulls `last_started_at`, so until the scheduler claims the row the
 * job looks like it has never started. This note is what keeps that gap from
 * reading as "never run". */
export function requestedNote(pollSeconds: number): string {
  return `requested — picks up within ${pollSeconds}s`;
}
