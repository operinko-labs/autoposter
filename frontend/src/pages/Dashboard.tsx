import { useEffect, useRef, useState } from "react";

import { apiFetch, apiFetchNdjson } from "../api/client";
import {
  JOB_STATES,
  isDashboardSnapshot,
  type EventEntry,
  type FullPassResponse,
  type ScheduledRunRequestResponse,
  type Status,
} from "../api/types";
import { formatTime } from "../format";
import { NOT_SCHEDULED_TITLE, requestedNote } from "../scheduledRuns";
import "./dashboard.css";

/** How long to wait before reconnecting a dropped stream -- the log tail's
 * interval, for the same reason. */
const RECONNECT_MS = 3000;

export function Dashboard() {
  const [status, setStatus] = useState<Status | null>(null);
  const [events, setEvents] = useState<EventEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [passBusy, setPassBusy] = useState(false);
  const [passOutcome, setPassOutcome] = useState<FullPassResponse | null>(null);
  // Keyed by job name rather than page-level: several rows have their own
  // button, and an error or a note belongs to the row that produced it.
  const [runBusy, setRunBusy] = useState<string | null>(null);
  const [runNotes, setRunNotes] = useState<Record<string, number>>({});
  const [runErrors, setRunErrors] = useState<Record<string, string>>({});

  // A ref rather than the stream effect's abort signal because the full-pass
  // response lands in a click handler that cannot reach it -- the same
  // reasoning as the Failures page.
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

  /** Mark one periodic job due. The endpoint neither starts nor waits, and it
   * is NOT idempotent against a run already in flight -- pressing twice
   * queues a second copy of the pass on the next poll. The button is disabled
   * for the duration of the request for that reason, and nothing here fires
   * it automatically. The stream below pushes whatever the scheduler does
   * next, so there is no extra re-read to do. */
  async function runNow(name: string) {
    setRunBusy(name);
    setRunErrors((previous) => {
      const next = { ...previous };
      delete next[name];
      return next;
    });
    try {
      const outcome = await apiFetch<ScheduledRunRequestResponse>(
        `/api/scheduled-runs/${name}/run`,
        { method: "POST" }
      );
      if (live.current) setRunNotes((previous) => ({ ...previous, [name]: outcome.poll_seconds }));
    } catch (caught) {
      if (live.current) setRunErrors((previous) => ({ ...previous, [name]: (caught as Error).message }));
    } finally {
      if (live.current) setRunBusy(null);
    }
  }

  // The server pushes a whole status+events snapshot whenever it changes, so
  // there is no polling here and no per-tick re-read: the first line of the
  // stream paints the page and every later line replaces it wholesale. The
  // reconnect is the log tail's, minus its clear-on-connect -- the tail
  // replays a buffer, whereas each snapshot here is self-contained, so the
  // last one stays on screen while a dropped stream is re-established rather
  // than blanking the page for three seconds.
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function connect() {
      try {
        setConnected(true);
        await apiFetchNdjson(
          "/api/dashboard/stream",
          (value) => {
            if (!isDashboardSnapshot(value)) return; // heartbeat
            setStatus(value.status);
            setEvents(value.events);
            setError(null);
          },
          controller.signal,
        );
      } catch (caught) {
        // A 401 is already handled centrally by apiFetchNdjson, which drops
        // the session and sends the user to the login form; the session is
        // checked once, at connect, so an expired one surfaces here as a
        // failed reconnect.
        if (controller.signal.aborted) return;
        setError((caught as Error).message);
      }
      if (controller.signal.aborted) return;
      setConnected(false);
      timer = setTimeout(() => void connect(), RECONNECT_MS);
    }

    void connect();
    return () => {
      controller.abort();
      if (timer !== undefined) clearTimeout(timer);
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
          <span className={connected ? "stream-status live" : "stream-status"}>
            {connected ? "live" : "reconnecting…"}
          </span>
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
                  <th />
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
                    <td className="scheduled-actions">
                      <button
                        type="button"
                        // A null interval means the running scheduler has no
                        // such job: this row survives from a configuration
                        // that registered it. Marking it due would leave a
                        // permanently-due row nothing ever claims.
                        disabled={job.interval_seconds === null || runBusy === job.name}
                        title={job.interval_seconds === null ? NOT_SCHEDULED_TITLE : undefined}
                        onClick={() => void runNow(job.name)}
                      >
                        {runBusy === job.name ? "Requesting…" : "Run now"}
                      </button>
                      {runNotes[job.name] !== undefined && (
                        <span className="muted">{requestedNote(runNotes[job.name])}</span>
                      )}
                      {runErrors[job.name] !== undefined && (
                        <span className="row-error">{runErrors[job.name]}</span>
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
