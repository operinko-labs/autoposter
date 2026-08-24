import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import type { CancelJobResponse, QueuedJob, QueuedJobsResponse } from "../api/types";
import { formatTime } from "../format";
import "./jobs.css";

/** How often the list is re-read. Polled rather than streamed: this is a small
 * JSON body on a page an operator has open while watching a pass, and the
 * dashboard's push stream exists because it fans one computed snapshot out to
 * every viewer -- a second stream for a plain list would be machinery without
 * a reason. */
const POLL_MS = 5000;

/** How long until the next attempt, as a phrase.
 *
 * Rendered once per poll and never ticked: a per-second countdown would
 * re-render the whole table sixty times a minute to move a number the operator
 * is not watching that closely, and the value is refreshed by the poll anyway.
 *
 * Rounded up, because "in 3m" should mean "within three minutes". Negative for
 * a job that is already due, which is every running job -- those read "now",
 * not "in -1m".
 */
function formatCountdown(seconds: number): string {
  if (seconds <= 0) return "now";
  if (seconds < 60) return `in ${seconds}s`;
  if (seconds < 3600) return `in ${Math.ceil(seconds / 60)}m`;
  return `in ${Math.ceil(seconds / 3600)}h`;
}

/** The season/episode suffix Plex users read titles by, or null when the job
 * is not for an episode or a season. */
function seasonEpisode(job: QueuedJob): string | null {
  const { season_number: season, episode_number: episode } = job;
  if (season === null) return null;
  const padded = String(season).padStart(2, "0");
  if (episode === null) return `S${padded}`;
  return `S${padded}E${String(episode).padStart(2, "0")}`;
}

/** A per-row outcome from the last cancel on that row.
 *
 * Kept per row rather than at page level because both outcomes are about one
 * job: the note explaining that a running job finishes its attempt first, and
 * the refusal when the queue had already moved the job somewhere terminal.
 */
type RowMessage = { tone: "note" | "error"; text: string };

export function Jobs() {
  const [jobs, setJobs] = useState<QueuedJob[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Record<number, RowMessage>>({});

  // `load` runs from the mount effect, from the poll, and from a click
  // handler, so a response can land after the page has gone. One ref covers
  // all three callers -- the per-effect `cancelled` local the sibling pages
  // use cannot reach a click handler. Failures.tsx's pattern.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const response = await apiFetch<QueuedJobsResponse>("/api/jobs");
      if (!live.current) return;
      setJobs(response.jobs);
      setTotal(response.total);
      setError(null);
    } catch (caught) {
      if (!live.current) return;
      setError((caught as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  async function cancel(job: QueuedJob) {
    // No confirmation: a pending job that should not have been cancelled can
    // be queued again by the same path that queued it, and a running one is
    // only asked to stop retrying. Nothing here destroys artwork.
    setBusyId(job.id);
    try {
      const response = await apiFetch<CancelJobResponse>(`/api/jobs/${job.id}/cancel`, {
        method: "POST",
      });
      if (live.current && response.cancel_requested === true) {
        // The running case is not a refusal and must not read as one: the
        // request was accepted, and this says what "accepted" means for a job
        // a worker is already inside.
        setMessages((prev) => ({
          ...prev,
          [job.id]: {
            tone: "note",
            text: response.detail ?? "the job will finish its current attempt",
          },
        }));
      }
    } catch (caught) {
      if (live.current) {
        setMessages((prev) => ({
          ...prev,
          [job.id]: { tone: "error", text: (caught as Error).message },
        }));
      }
    } finally {
      // Re-read whatever happened. An optimistic removal would show a job gone
      // even where the server declined to move it, and after a refusal the
      // list is exactly what says where the job really went.
      await load();
      if (live.current) setBusyId(null);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Jobs</h1>
        {jobs !== null && (
          <span className="muted">
            {total} pending or running job{total === 1 ? "" : "s"}
            {jobs.length < total ? ` (showing ${jobs.length})` : ""}
          </span>
        )}
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      <div className="panel">
        {jobs === null ? (
          <p className="muted">Loading…</p>
        ) : jobs.length === 0 ? (
          <p className="empty">No pending or running jobs.</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>State</th>
                  <th>Job</th>
                  <th>Item</th>
                  <th>Attempts</th>
                  <th>Next try</th>
                  <th>Queued</th>
                  <th>Last error</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const suffix = seasonEpisode(job);
                  const message = messages[job.id];
                  return (
                    <tr key={job.id}>
                      <td>
                        <span className={`pill pill-${job.state}`}>{job.state}</span>
                      </td>
                      <td>
                        {job.kind}
                        <span className="muted mono"> #{job.id}</span>
                      </td>
                      <td>
                        {job.title ?? <span className="muted">—</span>}
                        {suffix !== null && <span className="muted mono"> {suffix}</span>}
                      </td>
                      <td className="cell-time">
                        {job.attempts}/{job.max_attempts}
                        {job.waiting_for_plex && (
                          /* The badge and the larger budget travel together:
                             4/10 without the reason looks like a different
                             queue, and the reason without the count looks
                             like a failure. */
                          <span className="jobs-waiting">waiting for Plex</span>
                        )}
                      </td>
                      <td className="muted cell-time">
                        {formatCountdown(job.run_in_seconds)}
                      </td>
                      <td className="muted cell-time">{formatTime(job.created_at)}</td>
                      {/* Unbounded provider error strings, same as Failures:
                          unwrapped they widen the table past the viewport and
                          take the row's own button off screen with it. */}
                      <td className="mono cell-wrap">
                        {job.last_error ?? "—"}
                        {message !== undefined && (
                          <span className={`jobs-message jobs-message-${message.tone}`}>
                            {message.text}
                          </span>
                        )}
                      </td>
                      <td>
                        <div className="row-actions">
                          {job.cancel_requested && (
                            // Read from the row itself, not the client-only
                            // message: a page reload still shows a cancel that
                            // is pending, rather than losing that state.
                            <span className="jobs-cancelling">cancelling…</span>
                          )}
                          <button
                            type="button"
                            disabled={busyId === job.id || job.cancel_requested}
                            onClick={() => void cancel(job)}
                          >
                            Cancel
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
