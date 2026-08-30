import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import type { FactsBackfillStatus, FactsBackfillTrigger } from "../api/types";

/** Why the button exists (roadmap row 206): the packs that read the newer
 * facts columns (origin country, original language) work off a table the
 * drift sweep only revisits ~500 items a week — a settled library converges
 * in about five weeks. Each press enqueues the next batch of the same
 * per-item job the pipeline already runs, so the catch-up takes hours. */
const NOTE =
  "The location and language packs read facts columns that fill on the weekly " +
  "drift sweep — about five weeks for a settled library. Each press enqueues " +
  "the next batch (the sweep's own batch size) through the normal pipeline; " +
  "press again until it reports complete. The Jobs page shows the queue " +
  "working through them.";

/** The park is only as fresh as the press, and the copy says so.
 *
 * The trigger refuses to spend while TMDb's shared 429 window is open
 * (api/facts_backfill.py), but the workers drain the batch afterwards: a
 * refusal that starts between the press and the gather lets those items
 * through with the new columns still empty, and the cursor has already moved
 * past them, so pressing again resumes ahead rather than revisiting. The
 * weekly sweep is what eventually collects them — which is the same five-week
 * wait this button exists to shorten, for a batch rather than a library. An
 * operator who did not know this would read "complete" as "every item has its
 * columns", which is the one claim the walk cannot make. */
const PARK_NOTE =
  "A press is refused up front while TMDb is inside its 429 window, but the " +
  "check is only as fresh as the press: if TMDb starts refusing after a batch " +
  "is enqueued, those items are walked with the columns still empty and the " +
  "next press resumes past them. The weekly sweep is what collects those.";

/** The one-shot facts catch-up: standing progress plus one button.
 *
 * The server is the authority on progress: every press POSTs once and then
 * re-reads the GET rather than patching state from the response — the same
 * re-read posture GroupsPanel takes after a save. "Parked" (TMDb's shared
 * 429 window is open) and "complete" are statuses the server answers with,
 * not errors, so they render as notes; only a failed request is an error.
 */
export function FactsBackfillPanel() {
  const [status, setStatus] = useState<FactsBackfillStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // A ref rather than an effect-local `cancelled`: the trigger handler's
  // continuation lands outside any effect — the neighbouring panels' idiom.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    const next = await apiFetch<FactsBackfillStatus>("/api/facts/backfill");
    if (live.current) setStatus(next);
  }, []);

  useEffect(() => {
    reload().catch((caught: Error) => {
      if (live.current) setError(caught.message);
    });
  }, [reload]);

  async function trigger() {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const outcome = await apiFetch<FactsBackfillTrigger>("/api/facts/backfill", {
        method: "POST",
      });
      if (live.current) setNote(outcome.detail);
      // Re-read rather than derive: the GET is the same arithmetic the next
      // page load will show, so showing anything else here would be a lie
      // with a short shelf life.
      await reload();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBusy(false);
    }
  }

  const progress =
    status === null
      ? "Loading…"
      : `${status.done} of ${status.total} movies and shows walked` +
        (status.status === "complete" ? " — complete." : ".");

  return (
    <section className="panel">
      <div className="page-header">
        <h2>Facts backfill</h2>
        <button
          type="button"
          disabled={busy || status?.status === "complete"}
          onClick={() => void trigger()}
        >
          {busy ? "Enqueuing…" : "Enqueue next batch"}
        </button>
      </div>
      <p className="muted">{NOTE}</p>
      <p className="muted">{PARK_NOTE}</p>
      <p className="muted">{progress}</p>
      {note !== null && (
        <p className="muted" role="status">
          {note}
        </p>
      )}
      {error !== null && <p className="page-error">{error}</p>}
    </section>
  );
}
