/** One banner, shared by every tab, for the settings waiting on a restart.
 *
 * The list is the SERVER's (`GET /api/config`'s `restart_paths`, kept in the
 * stored document's metadata), so it survives a reload and shows to a second
 * admin -- which is the whole reason it is not component state. It is also the
 * reason nothing here accumulates a save response's `restart_required`: that
 * field is one write's difference against the running generation, while the
 * stored list is measured against what this process BOOTED on, and the two
 * disagree the moment a setting is edited and then put back.
 *
 * A refusal is rendered as the server's own sentence and nothing retries. The
 * four refusals name a run, a mode, a restart already under way or a
 * multi-worker deployment, and every one of them is a thing the operator has
 * to decide about rather than a transient the page should paper over.
 */
import { useState } from "react";

import { ApiError, apiFetch } from "../api/client";

export function RestartBanner({
  paths,
  onRestarted,
}: {
  paths: string[];
  /** Re-read `/api/config`. The list this banner renders is the server's, so
   * the only way it goes away is the server saying it has. */
  onRestarted: () => Promise<void> | void;
}) {
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  if (paths.length === 0) return null;

  async function restart() {
    setBusy(true);
    setRefusal(null);
    try {
      await apiFetch("/api/system/restart", { method: "POST" });
    } catch (caught) {
      setRefusal(
        caught instanceof ApiError && typeof caught.detail === "string"
          ? caught.detail
          : (caught as Error).message,
      );
      setBusy(false);
      return;
    }
    // The process is on its way down as that response lands, so the re-read
    // below is allowed to fail: what it would report is the service being
    // away, which is the thing that was just asked for, and reporting it as an
    // error would blame the restart for working. The button stays disabled
    // either way -- there is nothing left to press until this page is loaded
    // again, and a second press would only be answered "a restart is already
    // under way".
    try {
      await onRestarted();
    } catch {
      // Deliberately silent; see above.
    }
  }

  return (
    <section className="panel settings-restart-banner" role="status">
      <div className="settings-restart-banner-text">
        <p className="config-restart">
          {`These settings are saved and take effect at the next restart: ${paths.join(", ")}`}
        </p>
        {busy && (
          <p className="muted">
            Restarting. This page will need loading again once the service is
            back.
          </p>
        )}
        {refusal !== null && <p className="page-error">{refusal}</p>}
      </div>
      <button type="button" onClick={() => void restart()} disabled={busy}>
        {busy ? "Restarting…" : refusal === null ? "Restart now" : "Try again"}
      </button>
    </section>
  );
}
