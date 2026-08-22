import { useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import { JOB_STATES, type EventsResponse, type FullPassResponse, type Status } from "../api/types";
import { formatTime } from "../format";
import "./dashboard.css";

/** Polling, not a WebSocket: no socket endpoint exists yet, and adding one is
 * a server change that belongs with the other phase 4c endpoints. */
const POLL_MS = 5000;

export function Dashboard() {
  const [status, setStatus] = useState<Status | null>(null);
  const [events, setEvents] = useState<EventsResponse["events"]>([]);
  const [error, setError] = useState<string | null>(null);
  const [passBusy, setPassBusy] = useState(false);
  const [passOutcome, setPassOutcome] = useState<FullPassResponse | null>(null);

  // A ref rather than the polling effect's `cancelled` local because the
  // full-pass response lands in a click handler that local cannot reach --
  // the same reasoning as the Failures page.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  async function runFullPass() {
    setPassBusy(true);
    setError(null);
    try {
      // What is shown afterwards is the server's own count of what it
      // queued and what was already pending -- never an optimistic claim.
      const outcome = await apiFetch<FullPassResponse>("/api/full-pass", { method: "POST" });
      if (live.current) setPassOutcome(outcome);
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setPassBusy(false);
    }
  }

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [nextStatus, nextEvents] = await Promise.all([
          apiFetch<Status>("/api/status"),
          apiFetch<EventsResponse>("/api/events?limit=25"),
        ]);
        // The interval keeps firing while a request is in flight, and the tab
        // may be closed mid-request; without this the response lands on an
        // unmounted component.
        if (cancelled) return;
        setStatus(nextStatus);
        setEvents(nextEvents.events);
        setError(null);
      } catch (caught) {
        // A 401 is already handled centrally by apiFetch, which drops the
        // session and sends the user to the login form.
        if (!cancelled) setError((caught as Error).message);
      }
    }

    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (status === null) {
    return (
      <>
        <div className="page-header">
          <h1>Dashboard</h1>
        </div>
        {error !== null ? <p className="page-error">{error}</p> : <p className="muted">Loading…</p>}
      </>
    );
  }

  return (
    <>
      <div className="page-header">
        <h1>Dashboard</h1>
        <div className="header-actions">
          <span className="muted">
            {status.workers} worker{status.workers === 1 ? "" : "s"} · {status.processed_last_24h}{" "}
            processed in 24h
          </span>
          <button type="button" disabled={passBusy} onClick={() => void runFullPass()}>
            {passBusy ? "Running…" : "Run full pass"}
          </button>
        </div>
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      {passOutcome !== null && (
        <p className="muted">
          Full pass: {passOutcome.queued} queued, {passOutcome.skipped} already queued (
          {passOutcome.total} item{passOutcome.total === 1 ? "" : "s"}).
        </p>
      )}

      <div className="stat-grid">
        {JOB_STATES.map((state) => (
          <div className={`stat stat-${state}`} key={state}>
            <div className="stat-value">{status.jobs_by_state[state] ?? 0}</div>
            <div className="stat-label">{state}</div>
          </div>
        ))}
      </div>

      <div className="dashboard-columns">
        <section className="panel">
          <h2>Scheduled runs</h2>
          {status.scheduled_jobs.length === 0 ? (
            <p className="empty">Nothing scheduled.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Last run</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {status.scheduled_jobs.map((job) => (
                  <tr key={job.name}>
                    <td>{job.name}</td>
                    <td className="muted">{formatTime(job.last_finished_at)}</td>
                    <td>
                      {job.last_status === null ? (
                        <span className="muted">never</span>
                      ) : (
                        <span className={`pill pill-${job.last_status}`} title={job.last_detail ?? ""}>
                          {job.last_status}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="panel">
          <h2>Recent activity</h2>
          {events.length === 0 ? (
            <p className="empty">No events yet.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Source</th>
                  <th>Event</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event, index) => (
                  <tr key={`${event.received_at}-${index}`}>
                    <td className="muted">{formatTime(event.received_at)}</td>
                    <td>{event.source}</td>
                    <td>{event.event_type}</td>
                    <td>
                      <span className={`pill pill-${event.outcome}`}>{event.outcome}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </>
  );
}
