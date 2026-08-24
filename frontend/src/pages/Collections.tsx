import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import type {
  CollectionPosterResponse,
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

/** How often the completion watch re-reads status, matching the dashboard's
 * own poll so the two pages agree on how fresh "now" is. This is NOT a
 * page-wide poll: the interval exists only between an accepted Diff now and
 * that pass finishing (or the watch timing out). */
const POLL_MS = 5000;

/** How long past the scheduler's own pickup interval the watch keeps waiting,
 * in seconds. A reconcile that outlasts this is slow -- a big library, a
 * rate-limited Plex -- not failed, so the watch stops without claiming
 * anything and the requested note stays as it was. */
const WATCH_GRACE_SECONDS = 600;

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

/** One collection's row, plus the inline control that gives it a poster of the
 * operator's choosing.
 *
 * The control posts to `/api/collections/{id}/poster` (src/autoposter/api/manual.py),
 * which writes the file and stops -- the reconciler applies it. So the success
 * copy names Diff now, the button on this page that makes that pass happen
 * immediately rather than on the scheduler's next tick. An inline form in a row
 * of its own, the same idiom the render table uses for a clear-override note. */
function CollectionRow({ collection }: { collection: CollectionSummary }) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const diff = diffLabel(collection);

  async function setPoster() {
    setBusy(true);
    setNote(null);
    setFailure(null);
    try {
      await apiFetch<CollectionPosterResponse>(
        `/api/collections/${collection.id}/poster`,
        {
          method: "POST",
          // The one field the endpoint reads. A URL and a mount path are told
          // apart by their own shape server-side.
          body: JSON.stringify({ source }),
        },
      );
      // Named deliberately: the endpoint does not upload to Plex, it writes the
      // override the reconciler picks up. Diff now is how the operator makes
      // that pass run at once instead of waiting for the schedule.
      setNote(
        "Poster installed. It applies on the next reconcile — use Diff now to apply it immediately.",
      );
    } catch (caught) {
      // Verbatim: a 422 names a bad mount path, a 502 says the URL would not
      // serve an image, a 503 carries the OS error for the assets mount. This
      // page is behind require_session.
      setFailure((caught as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <tr>
        {/* Against four rigid columns the title was the only one that could
            give, so it collapsed to a word a line. */}
        <td className="cell-title">{collection.title}</td>
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
        <td>
          <button
            type="button"
            className="set-poster"
            onClick={() => setOpen((value) => !value)}
          >
            Set poster…
          </button>
        </td>
      </tr>
      {open && (
        <tr className="poster-form-row">
          <td colSpan={6}>
            <p className="poster-help muted">
              Paste an <span className="mono">https://…</span> URL, or a path under{" "}
              <span className="mono">/manualassets</span>.
            </p>
            <div className="poster-form">
              <input
                type="text"
                className="poster-input"
                value={source}
                placeholder="https://… or /manualassets/…"
                onChange={(event) => setSource(event.target.value)}
              />
              <button
                type="button"
                disabled={busy || source.trim() === ""}
                onClick={() => void setPoster()}
              >
                Install
              </button>
            </div>
            {note !== null && <p className="poster-note">{note}</p>}
            {failure !== null && <p className="poster-error">{failure}</p>}
          </td>
        </tr>
      )}
    </>
  );
}

export function Collections() {
  const [collections, setCollections] = useState<CollectionSummary[] | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [requestedPoll, setRequestedPoll] = useState<number | null>(null);
  // The completion watch for an outstanding Diff now. A fresh object per
  // request even when the fields match, so the effect below tears the
  // previous interval down and a second press cannot leave two running.
  const [watch, setWatch] = useState<{ finishedAt: string | null; ticks: number } | null>(null);

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
    return next;
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

  /** Watch an outstanding request until its pass lands, then show what it
   * changed -- the page's promise is that Diff now produces a result without
   * a reload.
   *
   * `last_finished_at` moving is the only completion signal the server gives:
   * the run-now endpoint nulls `last_started_at` and nothing else, and the
   * scheduler stamps the finish when the pass returns. Null to non-null
   * counts as movement -- a job that had never finished has now. */
  useEffect(() => {
    if (watch === null) return;
    let elapsed = 0;
    const timer = setInterval(() => {
      elapsed += 1;
      // Out of patience, not out of hope: a reconcile slower than this is a
      // big library or a rate-limited Plex, so stop quietly and leave the
      // requested note standing rather than claiming a failure.
      if (elapsed > watch.ticks) {
        setWatch(null);
        return;
      }
      void (async () => {
        let next: Status;
        try {
          next = await apiFetch<Status>("/api/status");
        } catch {
          // A status read that failed is not the user's request failing.
          // Leave the page as it is and try again on the next tick.
          return;
        }
        if (!live.current) return;
        const job = next.scheduled_jobs.find((candidate) => candidate.name === RECONCILE_JOB);
        if ((job?.last_finished_at ?? null) === watch.finishedAt) {
          setStatus(next);
          return;
        }
        // The pass has landed, so the member counts and the +N −M deltas are
        // stale in the table: re-read them rather than leaving the user to
        // reload the page themselves.
        try {
          const response = await apiFetch<CollectionsResponse>("/api/collections");
          if (live.current) setCollections(response.collections);
        } catch (caught) {
          if (live.current) setError((caught as Error).message);
        }
        if (!live.current) return;
        setStatus(next);
        setRequestedPoll(null);
        setWatch(null);
      })();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [watch]);

  const reconcile = status?.scheduled_jobs.find((job) => job.name === RECONCILE_JOB);
  const scheduled = reconcile !== undefined && reconcile.interval_seconds !== null;

  async function diffNow() {
    setBusy(true);
    setError(null);
    setRequestedPoll(null);
    // Any watch from an earlier press is superseded here, so its interval is
    // torn down rather than left running alongside the new one.
    setWatch(null);
    try {
      const outcome = await apiFetch<ScheduledRunRequestResponse>(
        `/api/scheduled-runs/${RECONCILE_JOB}/run`,
        { method: "POST" }
      );
      // Re-read rather than patching the row locally: the server has just
      // nulled last_started_at, and the status endpoint is the authority on
      // what the scheduler now thinks. It also carries the finish time the
      // watch has to see move. Reading `status` state instead would risk a
      // baseline stale from page load -- an autonomous pass that landed
      // while the tab sat open would have already moved it, and the watch
      // would mistake that old movement for this request completing. The
      // run-now endpoint only nulls `last_started_at`, so this read (made
      // after the POST) still carries the correct pre-request finish time.
      const fresh = await loadStatus();
      if (live.current) {
        const job = fresh.scheduled_jobs.find((candidate) => candidate.name === RECONCILE_JOB);
        setRequestedPoll(outcome.poll_seconds);
        setWatch({
          finishedAt: job?.last_finished_at ?? null,
          ticks: Math.ceil(((outcome.poll_seconds + WATCH_GRACE_SECONDS) * 1000) / POLL_MS),
        });
      }
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
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Library</th>
                  <th>Kind</th>
                  <th>Members</th>
                  <th>Last diff</th>
                  <th>Poster</th>
                </tr>
              </thead>
              <tbody>
                {collections.map((collection) => (
                  <CollectionRow key={collection.id} collection={collection} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
