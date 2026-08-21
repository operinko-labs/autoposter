import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import type { ParkedJob, ParkedJobsResponse } from "../api/types";
import { formatTime } from "../format";

export function Failures() {
  const [jobs, setJobs] = useState<ParkedJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  // `load` is awaited from the effect and again from a click handler, so a
  // response can land after the page has gone. A ref rather than a local flag
  // because the same guard has to cover both callers, and the sibling pages'
  // per-effect `cancelled` local cannot reach a click handler.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const response = await apiFetch<ParkedJobsResponse>("/api/jobs/parked");
      if (!live.current) return;
      setJobs(response.jobs);
      setError(null);
    } catch (caught) {
      if (!live.current) return;
      setError((caught as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(job: ParkedJob, action: "retry" | "dismiss") {
    setBusyId(job.id);
    setError(null);
    try {
      await apiFetch(`/api/jobs/${job.id}/${action}`, { method: "POST" });
      // Re-read rather than removing the row locally. An optimistic update
      // would show the job gone even when the server declined to move it,
      // and the queue is the authority on what state a job is now in.
      await load();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBusyId(null);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Failures</h1>
        {jobs !== null && (
          <span className="muted">
            {jobs.length} parked job{jobs.length === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      <div className="panel">
        {jobs === null ? (
          <p className="muted">Loading…</p>
        ) : jobs.length === 0 ? (
          <p className="empty">Nothing parked. </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Attempts</th>
                <th>Parked</th>
                <th>Reason</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id}>
                  <td>
                    {job.kind}
                    <span className="muted mono"> #{job.id}</span>
                  </td>
                  <td>{job.attempts}</td>
                  <td className="muted">{formatTime(job.updated_at)}</td>
                  <td className="mono">{job.reason ?? "—"}</td>
                  <td className="row-actions">
                    <button
                      type="button"
                      disabled={busyId === job.id}
                      onClick={() => void act(job, "retry")}
                    >
                      Retry
                    </button>
                    <button
                      type="button"
                      disabled={busyId === job.id}
                      onClick={() => void act(job, "dismiss")}
                    >
                      Dismiss
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
