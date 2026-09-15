/** What the mounted file is FOR, now that it is not a source of truth.
 *
 * Nothing is rendered when the file agrees with the store, and nothing is
 * rendered when there is no file at all -- removing the mounted file once the
 * store is seeded is the intended end state, and a permanent warning for
 * having done the right thing would be worse than no notice.
 *
 * Import and Export go through the endpoints that already exist, drop cap,
 * confirm and pre-write snapshot included. There is no import-only path here
 * and there must not be one.
 */
import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import type { ConfigExport, DriftResponse } from "../api/types";
import { EXPORT_WARNING } from "./ConfigSafetyPanel";

/** The design's own sentence, verbatim. */
export const DRIFT_NOTICE =
  "The configuration file on disk differs from the stored configuration.";

/** Why the confirm is here rather than implied by the button.
 *
 * The store holds the whole configuration and a mounted file is usually a
 * handful of settings, so importing one drops every setting it does not
 * mention -- which is precisely what the server's drop cap refuses unasked. A
 * page that sent `confirm: true` on the operator's behalf would turn that
 * guard back into the bug it was added for. */
const IMPORT_NOTE =
  "Importing replaces the stored configuration with the file's, so anything " +
  "the file does not set goes back to its default.";

export function DriftNotice({
  revision = null,
  onChanged,
}: {
  /** The revision the settings page seeded from, sent with the import for the
   * reason every other write on that page sends it: one composed against a
   * page that has gone stale is a lost update. Absent -- the component
   * rendered on its own -- means the server is not asked to check. */
  revision?: string | null;
  /** Re-read `/api/config` and re-adopt, exactly as a save does. */
  onChanged: () => Promise<void> | void;
}) {
  const [drift, setDrift] = useState<DriftResponse | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [download, setDownload] = useState<{ href: string; name: string } | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    apiFetch<DriftResponse>("/api/config/drift")
      .then((response) => {
        if (!cancelled) setDrift(response);
      })
      .catch(() => {
        // A drift report that cannot be fetched is not worth a page error:
        // the page's job is the configuration, and this is a footnote about a
        // file. It simply does not render.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (drift === null || !drift.file_present || !drift.differs) return null;
  // Served by the route that just reported the difference, so the import
  // sends the very document that was compared -- there is no second read of
  // the file and no chance of importing a different one. A response that
  // carries none leaves the button disabled rather than posting an empty
  // document, which the drop cap would read as "clear everything".
  const fileDocument = drift.document ?? null;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(
        caught instanceof ApiError && typeof caught.detail === "string"
          ? caught.detail
          : (caught as Error).message,
      );
    } finally {
      setBusy(false);
      // A tick made for this press does not carry over to the next one, win
      // or lose -- the drop cap it disarms must be re-armed by intent.
      setConfirm(false);
    }
  }

  async function importFile() {
    if (fileDocument === null) return;
    await run(async () => {
      // The envelope the export writes, because the import endpoint reads
      // that shape and no other.
      await apiFetch("/api/config/overrides/import", {
        method: "POST",
        body: JSON.stringify({
          autoposter_overrides: 1,
          document: fileDocument,
          confirm,
          ...(revision === null ? {} : { expected_revision: revision }),
        }),
      });
      await onChanged();
      setDrift(await apiFetch<DriftResponse>("/api/config/drift"));
    });
  }

  async function exportStore() {
    await run(async () => {
      // The same two steps the backup panel takes, and for the same reason:
      // this endpoint answers only to a bearer header, so a plain link to it
      // would download nothing.
      const envelope = await apiFetch<ConfigExport>("/api/config/overrides/export");
      const blob = new Blob([JSON.stringify(envelope, null, 2)], {
        type: "application/json",
      });
      const stamp = envelope.exported_at.replace(/[:.]/g, "-");
      // The previous download's URL has served its purpose the moment a new
      // one replaces it; only the object it names would otherwise outlive the
      // click that made it.
      if (download !== null) URL.revokeObjectURL(download.href);
      setDownload({
        href: URL.createObjectURL(blob),
        name: `autoposter-overrides-${stamp}.json`,
      });
    });
  }

  return (
    <section className="panel config-drift" role="status">
      <h2>Configuration file</h2>
      <p>{DRIFT_NOTICE}</p>
      {/* An empty list with a difference reported is the server saying it
          could not walk the two side by side -- one of them no longer builds,
          so there is no second document to name paths from. "The file and the
          store agree" would be the false sentence; this is the true one. */}
      <p className="muted">
        {drift.paths.length > 0
          ? `Differing settings: ${drift.paths.join(", ")}`
          : "The differing settings cannot be listed: one of the two no longer describes a configuration this service can build."}
      </p>
      {drift.path !== null && <p className="muted mono">{drift.path}</p>}
      <p className="muted">{IMPORT_NOTE}</p>
      <p className="config-safety-warning">{EXPORT_WARNING}</p>

      {error !== null && <p className="page-error">{error}</p>}

      <label className="config-drift-confirm">
        <input
          type="checkbox"
          checked={confirm}
          onChange={(event) => setConfirm(event.target.checked)}
        />
        Allow the import to remove settings I have saved
      </label>

      <div className="config-drift-actions">
        <button
          type="button"
          disabled={busy || fileDocument === null}
          onClick={() => void importFile()}
        >
          Import the file
        </button>
        <button type="button" disabled={busy} onClick={() => void exportStore()}>
          Export the store
        </button>
      </div>

      {download !== null && (
        <a
          className="config-safety-download"
          data-testid="drift-export-link"
          href={download.href}
          download={download.name}
        >
          {`Save ${download.name}`}
        </a>
      )}
    </section>
  );
}
