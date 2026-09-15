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

/** Why an unsaved edit blocks the button rather than surviving it.
 *
 * Two things go wrong otherwise, and the second is worse than the first. The
 * re-read that follows a restart re-seeds the editor from the server, which
 * silently throws the typing away; and the sentence above the button says the
 * settings that are waiting take effect at the next restart, which invites the
 * reading that the edit on screen is one of them. It is not -- nothing
 * unsaved is in the store, and a restart reads the store. Refusing the press
 * says both of those things at once, and the pending bar is already on screen
 * with the two ways out of it.
 *
 * Exported because two other controls on the System tab do the same re-read
 * and need the same refusal: the drift notice's import, and the backup
 * panel's restore and import. One rule and one sentence for all three -- three
 * wordings of one rule is how an operator learns it as three rules. It names
 * the stored settings rather than a restart for that reason: what the three
 * have in common is that they act on what is stored, and nothing unsaved is. */
export const PENDING_EDITS_NOTE =
  "Save or discard the changes below first — this acts on the stored " +
  "settings, and anything unsaved would be lost.";

export function RestartBanner({
  paths,
  pendingEdits = false,
  onRestarted,
}: {
  paths: string[];
  /** Whether the editor is holding an edit nobody has stored. */
  pendingEdits?: boolean;
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
        {pendingEdits && <p className="muted">{PENDING_EDITS_NOTE}</p>}
        {refusal !== null && <p className="page-error">{refusal}</p>}
      </div>
      <button
        type="button"
        onClick={() => void restart()}
        disabled={busy || pendingEdits}
      >
        {busy ? "Restarting…" : refusal === null ? "Restart now" : "Try again"}
      </button>
    </section>
  );
}
