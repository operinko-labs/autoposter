import type { ScheduledRun } from "../api/types";
import { formatSince } from "../format";

/** The scheduled-runs table's derived-status pill (api/snapshots.py's
 * `_run_status`): "never" for a job that has not run, Running with how long
 * it has been going, Interrupted with why, or the recorded outcome pill
 * otherwise -- the same four shapes `job.status` can take (see api/types.ts).
 *
 * Shared between the Dashboard's own scheduled-runs table and the
 * Collections page's reconcile bar, which renders the *same*
 * `collections_reconcile` row off the same `/api/status` payload -- see
 * Collections.tsx. Before this pill existed, the reconcile bar rendered
 * `last_status` directly and showed the previous run's outcome for the whole
 * duration of a long reconcile, the exact stale-outcome shape the Dashboard's
 * derived `status` field exists to kill. */
export function ScheduledRunStatusPill({ job }: { job: ScheduledRun }) {
  if (job.status === null) {
    return <span className="muted">never</span>;
  }
  if (job.status === "running") {
    return (
      <>
        <span className="pill pill-running">Running</span>{" "}
        {job.last_started_at !== null && (
          <span className="muted">{formatSince(job.last_started_at)}</span>
        )}
      </>
    );
  }
  if (job.status === "interrupted") {
    return (
      <span
        className="pill pill-interrupted"
        title="started before this instance; the run died with its process"
      >
        Interrupted
      </span>
    );
  }
  return (
    <span className={`pill pill-${job.status}`} title={job.last_detail ?? ""}>
      {job.status}
    </span>
  );
}
