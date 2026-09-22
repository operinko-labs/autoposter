import { lazy, Suspense, useEffect, useRef, useState } from "react";

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
import { ScheduledRunStatusPill } from "./ScheduledRunStatus";
import "./dashboard.css";

/** Lazy, so recharts -- the largest dependency in the bundle, used by this
 * one component -- leaves the entry chunk and downloads only when a dashboard
 * is actually drawn (perf spec A3). */
const RunCharts = lazy(() =>
  import("./RunCharts").then((module) => ({ default: module.RunCharts })),
);

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

  // The Running pill's "since <relative>" label is computed at render time
  // from `formatSince`'s default `now`, so it only advances when something
  // else re-renders the page -- and the stream only pushes a new snapshot
  // when the *encoded* status changes, which a long run on a quiet queue does
  // not do between claim and finish. Without this, a 15-minute run could read
  // "since 2m" for thirteen of those minutes. Bounded to when a row is
  // actually running, and torn down otherwise, so a quiet dashboard holds no
  // interval.
  const hasRunningJob = status?.scheduled_jobs.some((job) => job.status === "running") ?? false;
  const [, forceSinceTick] = useState(0);
  useEffect(() => {
    if (!hasRunningJob) return;
    const id = setInterval(() => forceSinceTick((tick) => tick + 1), 60000);
    return () => clearInterval(id);
  }, [hasRunningJob]);

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
  //
  // A hidden tab holds no stream (perf spec A8): the server polls the
  // database every two seconds for as long as anyone is subscribed, and a
  // background tab is nobody. Hiding aborts the stream and any pending
  // reconnect; showing reconnects through the same connect() a drop uses,
  // and the last snapshot stays on screen in between.
  useEffect(() => {
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function connect() {
      const own = new AbortController();
      controller = own;
      try {
        // Deliberately NOT setError(null) here, unlike Logs.tsx's connect:
        // this page's error state is shared with the run-now and full-pass
        // handlers, and a reconnect must not erase an action failure the
        // operator has not seen. The first snapshot clears it instead.
        setConnected(true);
        await apiFetchNdjson(
          "/api/dashboard/stream",
          (value) => {
            if (!isDashboardSnapshot(value)) return; // heartbeat
            setStatus(value.status);
            setEvents(value.events);
            setError(null);
          },
          own.signal,
        );
      } catch (caught) {
        // A 401 is already handled centrally by apiFetchNdjson, which drops
        // the session and sends the user to the login form; the session is
        // checked once, at connect, so an expired one surfaces here as a
        // failed reconnect.
        if (own.signal.aborted) return;
        setError((caught as Error).message);
      }
      if (own.signal.aborted) return;
      setConnected(false);
      timer = setTimeout(() => void connect(), RECONNECT_MS);
    }

    /** Stop reading: abort the open stream and cancel a pending reconnect. */
    function pause() {
      controller?.abort();
      controller = null;
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
    }

    function onVisibilityChange() {
      pause();
      if (!document.hidden) void connect();
    }

    if (!document.hidden) void connect();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      pause();
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

      <Suspense fallback={<p className="muted">Loading run history…</p>}>
        <RunCharts />
      </Suspense>

      <div className="dashboard-columns">
        <section className="panel">
          <h2>Scheduled runs</h2>
          {status.scheduled_jobs.length === 0 ? (
            <p className="empty">Nothing scheduled.</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Last run</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {status.scheduled_jobs.map((job) => (
                    <tr key={job.name}>
                      <td>{job.name}</td>
                      {/* Left to wrap, the timestamp took four lines and left
                          Run now beside it a sliver wide. */}
                      <td className="muted cell-time">{formatTime(job.last_finished_at)}</td>
                      <td>
                        <ScheduledRunStatusPill job={job} />
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
            </div>
          )}
        </section>

        <section className="panel">
          <h2>Recent activity</h2>
          {events.length === 0 ? (
            <p className="empty">No events yet.</p>
          ) : (
            <div className="table-scroll">
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
                      <td className="muted cell-time">{formatTime(event.received_at)}</td>
                      <td>{event.source}</td>
                      <td>{event.event_type}</td>
                      <td>
                        <span className={`pill pill-${event.outcome}`}>{event.outcome}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
