import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import type {
  CollectionSummary,
  CollectionsResponse,
  ScheduledRun,
  ScheduledRunRequestResponse,
  Status,
} from "../api/types";
import { formatTime } from "../format";
import { NOT_SCHEDULED_TITLE, requestedNote } from "../scheduledRuns";
// The status pill is dashboard.css's, and the reconcile bar shows the same
// job state the dashboard does -- imported rather than duplicated so the two
// pages cannot drift apart.
import "./dashboard.css";
import "./collections.css";

/** The scheduler job that reconciles managed collections
 * (scheduler/jobs.py). The endpoint 404s on anything outside its allowlist,
 * so this string has to match exactly. */
const RECONCILE_JOB = "collections_reconcile";

/** When the scheduler will next start the job on its own.
 *
 * The server sends no next-run time: it publishes `last_started_at` and the
 * cadence separately and leaves the arithmetic here. Null interval means the
 * job is not registered in this deployment, and a null `last_started_at`
 * means it has never started -- or that a run was just requested, since that
 * is how the request is made. Both are unknowable, not zero. */
function nextRefreshLabel(job: ScheduledRun | undefined): string {
  if (job === undefined || job.interval_seconds === null || job.last_started_at === null) {
    return "—";
  }
  const started = new Date(job.last_started_at);
  if (Number.isNaN(started.getTime())) return "—";
  return formatTime(new Date(started.getTime() + job.interval_seconds * 1000).toISOString());
}

/** The last pass's diff, or "—" where no pass has reported one.
 *
 * Null and 0 are different answers. A smart collection has null stats
 * forever -- Plex evaluates its filter live, so there is no membership for a
 * pass to diff -- and so does any row predating the migration that added the
 * columns. `+0 −0` means a pass ran and found nothing to change, which is
 * real information and must not be flattened into the same dash. */
function diffLabel(collection: CollectionSummary): string | null {
  if (collection.last_added === null || collection.last_removed === null) return null;
  return `+${collection.last_added} −${collection.last_removed}`;
}

export function Collections() {
  const [collections, setCollections] = useState<CollectionSummary[] | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [requestedPoll, setRequestedPoll] = useState<number | null>(null);

  // A ref rather than the effect's `cancelled` local: the run-now response
  // lands in a click handler that local cannot reach -- the same reasoning as
  // the dashboard and failures pages.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const loadStatus = useCallback(async () => {
    const next = await apiFetch<Status>("/api/status");
    if (live.current) setStatus(next);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([apiFetch<CollectionsResponse>("/api/collections"), loadStatus()])
      .then(([response]) => {
        if (!cancelled) setCollections(response.collections);
      })
      .catch((caught: Error) => {
        if (!cancelled) setError(caught.message);
      });
    return () => {
      cancelled = true;
    };
  }, [loadStatus]);

  const reconcile = status?.scheduled_jobs.find((job) => job.name === RECONCILE_JOB);
  const scheduled = reconcile !== undefined && reconcile.interval_seconds !== null;

  async function diffNow() {
    setBusy(true);
    setError(null);
    setRequestedPoll(null);
    try {
      const outcome = await apiFetch<ScheduledRunRequestResponse>(
        `/api/scheduled-runs/${RECONCILE_JOB}/run`,
        { method: "POST" }
      );
      if (live.current) setRequestedPoll(outcome.poll_seconds);
      // Re-read rather than patching the row locally: the server has just
      // nulled last_started_at, and the status endpoint is the authority on
      // what the scheduler now thinks.
      await loadStatus();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBusy(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Collections</h1>
        {collections !== null && <span className="muted">{collections.length} managed</span>}
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      {status !== null && (
        <div className="reconcile-bar">
          <span className="muted">{`Last refresh: ${formatTime(
            reconcile?.last_finished_at ?? null
          )}`}</span>
          {reconcile?.last_status == null ? (
            <span className="muted">never</span>
          ) : (
            <span className={`pill pill-${reconcile.last_status}`} title={reconcile.last_detail ?? ""}>
              {reconcile.last_status}
            </span>
          )}
          <span className="muted">{`Next refresh: ${nextRefreshLabel(reconcile)}`}</span>
          <button
            type="button"
            disabled={!scheduled || busy}
            title={scheduled ? undefined : NOT_SCHEDULED_TITLE}
            onClick={() => void diffNow()}
          >
            {busy ? "Requesting…" : "Diff now"}
          </button>
          {requestedPoll !== null && <span className="muted">{requestedNote(requestedPoll)}</span>}
        </div>
      )}

      <div className="panel">
        {collections === null ? (
          <p className="muted">Loading…</p>
        ) : collections.length === 0 ? (
          <p className="empty">No managed collections yet.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Library</th>
                <th>Kind</th>
                <th>Members</th>
                <th>Last diff</th>
              </tr>
            </thead>
            <tbody>
              {collections.map((collection) => {
                const diff = diffLabel(collection);
                return (
                  <tr key={collection.id}>
                    <td>{collection.title}</td>
                    <td className="muted">{collection.library}</td>
                    <td>{collection.kind}</td>
                    <td>{collection.member_count ?? "—"}</td>
                    <td>
                      {diff === null ? (
                        "—"
                      ) : (
                        <span title={formatTime(collection.last_reconciled_at)}>{diff}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
