/** Backup, history and restore for the overrides document.
 *
 * Its own file rather than another section of `Settings.tsx` -- that page is
 * already the largest in the app and this is a self-contained thing with its
 * own fetches, its own errors and its own state. It renders as one more panel
 * inside the settings page, not as a new route: an operator looking for "what
 * did my configuration used to be" is already on the page that changed it.
 *
 * Everything destructive here is gated on one checkbox the operator ticks,
 * which becomes the API's `confirm: true`. The page never sets it on their
 * behalf, and never retries a refusal -- the two habits that turn a guard back
 * into the bug it was added for.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import { fieldErrors, STALE_SAVE_NOTE } from "../api/overrides";
import type {
  ConfigExport,
  ConfigPreviewResponse,
  ConfigSaveResponse,
  ConfigSnapshot,
} from "../api/types";

/** Said where the decision is made, because the endpoint deliberately does not
 * redact: a redacted backup would write the bare notification host back over
 * the real URL on the next import, which is a broken backup wearing a safe
 * one's clothes. */
export const EXPORT_WARNING =
  "The backup file contains your settings in full, including the notification URL. " +
  "Keep it somewhere you would keep a password.";

function refusal(caught: unknown): string {
  if (caught instanceof ApiError && caught.status === 422) {
    const errors = fieldErrors(caught.detail);
    const messages = Object.entries(errors).map(([path, message]) =>
      path === "" || path === "document" ? message : `${path}: ${message}`,
    );
    if (messages.length > 0) return messages.join("; ");
  }
  return (caught as Error).message;
}

function describe(snapshot: ConfigSnapshot): string {
  const when = new Date(snapshot.created_at);
  const stamp = Number.isNaN(when.getTime())
    ? snapshot.created_at
    : when.toLocaleString();
  const settings = snapshot.path_count === 1 ? "1 setting" : `${snapshot.path_count} settings`;
  return `${settings} · ${stamp} · replaced by ${snapshot.reason}`;
}

interface PendingImport {
  envelope: ConfigExport;
  preview: ConfigPreviewResponse;
  name: string;
}

export function ConfigSafetyPanel({
  revision,
  onChanged,
}: {
  /** The revision the settings page seeded from. Sent with every write here
   * for the same reason it is sent with a save: a restore composed against a
   * page that has gone stale is the same lost update. */
  revision: string | null;
  /** Re-read `/api/config` and re-adopt. Provenance is the server's to report
   * after a restore exactly as it is after a save. */
  onChanged: () => Promise<void>;
}) {
  const [snapshots, setSnapshots] = useState<ConfigSnapshot[]>([]);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [download, setDownload] = useState<{ href: string; name: string } | null>(null);
  const [pending, setPending] = useState<PendingImport | null>(null);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const reload = useCallback(async () => {
    const rows = await apiFetch<ConfigSnapshot[]>("/api/config/snapshots");
    // A response that is not the array this endpoint promises (a page's own
    // test-only fetch stub answering every route with the same body, say) is
    // read as no history rather than crashing the panel that shows it.
    if (live.current) setSnapshots(Array.isArray(rows) ? rows : []);
  }, []);

  useEffect(() => {
    reload().catch((caught: Error) => {
      if (live.current) setError(caught.message);
    });
  }, [reload]);

  /** One body for both writes here, and the same shape the editor's save uses:
   * the confirm the operator ticked, and the revision the page seeded from. */
  function guards(): { confirm: boolean; expected_revision?: string } {
    return revision === null ? { confirm } : { confirm, expected_revision: revision };
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await action();
    } catch (caught) {
      if (!live.current) return;
      if (caught instanceof ApiError && caught.status === 409) {
        // Never retried. Re-read, and only claim the page shows current
        // settings once that re-read has actually happened -- a failed
        // re-read is a different, worse failure than the save it followed.
        try {
          await onChanged();
          if (live.current) setNote(STALE_SAVE_NOTE);
        } catch (reread) {
          if (live.current) setError((reread as Error).message);
        }
      } else {
        setError(refusal(caught));
      }
    } finally {
      if (live.current) {
        setBusy(false);
        // A tick made for this submit does not carry over to the next one,
        // win or lose -- the drop cap it disarms must be re-armed by intent.
        setConfirm(false);
      }
    }
  }

  async function restore(id: number) {
    await run(async () => {
      await apiFetch<ConfigSaveResponse>(`/api/config/snapshots/${id}/restore`, {
        method: "POST",
        body: JSON.stringify(guards()),
      });
      // Said only once the re-read it describes has actually happened --
      // otherwise it is a claim about settings the page has not yet seen.
      await onChanged();
      if (live.current) setNote("Restored. The settings below are the restored ones.");
      await reload();
    });
  }

  async function exportOverrides() {
    await run(async () => {
      const envelope = await apiFetch<ConfigExport>("/api/config/overrides/export");
      const blob = new Blob([JSON.stringify(envelope, null, 2)], {
        type: "application/json",
      });
      const stamp = envelope.exported_at.replace(/[:.]/g, "-");
      if (live.current) {
        // The previous download's URL has served its purpose the moment a
        // new one replaces it; only the object it names would otherwise
        // outlive the click that made it.
        if (download !== null) URL.revokeObjectURL(download.href);
        setDownload({ href: URL.createObjectURL(blob), name: `autoposter-overrides-${stamp}.json` });
      }
    });
  }

  async function choose(file: File | undefined) {
    if (file === undefined) return;
    setPending(null);
    setNote(null);
    let parsed: unknown;
    try {
      parsed = JSON.parse(await file.text());
    } catch {
      setError(`${file.name} could not be read as JSON.`);
      return;
    }
    const envelope = parsed as ConfigExport;
    if (
      envelope === null ||
      typeof envelope !== "object" ||
      envelope.autoposter_overrides !== 1 ||
      envelope.document === null ||
      typeof envelope.document !== "object"
    ) {
      // Checked here as well as on the server, because the two refusals say
      // different things: the server's is about a format, and this one is
      // about the file the operator just picked.
      setError(`${file.name} is not an Autoposter overrides backup.`);
      return;
    }
    await run(async () => {
      const preview = await apiFetch<ConfigPreviewResponse>("/api/config/preview", {
        method: "POST",
        body: JSON.stringify(
          revision === null
            ? { document: envelope.document }
            : { document: envelope.document, expected_revision: revision },
        ),
      });
      if (live.current) setPending({ envelope, preview, name: file.name });
    });
  }

  async function importPending() {
    if (pending === null) return;
    await run(async () => {
      await apiFetch<ConfigSaveResponse>("/api/config/overrides/import", {
        method: "POST",
        body: JSON.stringify({ ...pending.envelope, ...guards() }),
      });
      if (live.current) setPending(null);
      await onChanged();
      // Same reasoning as restore(): said only once the re-read has happened.
      if (live.current) setNote(`Imported ${pending.name}.`);
      await reload();
    });
  }

  return (
    <section className="panel config-safety">
      <h2>Backup and previous versions</h2>

      {error !== null && <p className="page-error">{error}</p>}
      {note !== null && <p className="config-saved">{note}</p>}

      <label className="config-safety-confirm">
        <input
          type="checkbox"
          checked={confirm}
          onChange={(event) => setConfirm(event.target.checked)}
        />
        Allow this to remove settings I have saved
      </label>
      <p className="muted">
        Restoring or importing an older set of settings removes anything saved
        since. Without this ticked, the server refuses a change that would
        remove more than a few.
      </p>

      <h3>Previous versions</h3>
      {snapshots.length === 0 ? (
        <p className="muted">No previous versions yet — one is kept before every save.</p>
      ) : (
        <ul className="config-safety-list">
          {snapshots.map((snapshot) => (
            <li key={snapshot.id}>
              <span>{describe(snapshot)}</span>
              <button type="button" disabled={busy} onClick={() => void restore(snapshot.id)}>
                Restore
              </button>
            </li>
          ))}
        </ul>
      )}

      <h3>Backup file</h3>
      <p className="config-safety-warning">{EXPORT_WARNING}</p>
      <button type="button" disabled={busy} onClick={() => void exportOverrides()}>
        Download a backup
      </button>
      {download !== null && (
        <a
          className="config-safety-download"
          data-testid="config-export-link"
          href={download.href}
          download={download.name}
        >
          {`Save ${download.name}`}
        </a>
      )}

      <label className="config-safety-import" htmlFor="config-import-file">
        Restore from a backup file
      </label>
      <input
        id="config-import-file"
        type="file"
        accept="application/json,.json"
        disabled={busy}
        onChange={(event) => {
          void choose(event.target.files?.[0]);
          // Reset so picking the same file again fires another change event.
          event.target.value = "";
        }}
      />
      {pending !== null && (
        <>
          <p className="muted">
            {`${pending.name} is a valid backup. ${
              pending.preview.restart_required.length > 0
                ? `Restart required to apply: ${pending.preview.restart_required.join(", ")}.`
                : "Nothing in it needs a restart."
            }`}
          </p>
          <button type="button" disabled={busy} onClick={() => void importPending()}>
            Import these settings
          </button>
        </>
      )}
    </section>
  );
}
