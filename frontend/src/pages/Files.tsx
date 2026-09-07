import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../api/client";
import type { AssetFile, AssetFilesResponse } from "../api/types";
// `.mode-confirm` and `.primary` are Modes.tsx's two-step confirm idiom --
// reused rather than duplicated, exactly as MetadataOverridesPanel.tsx reuses
// them. `.primary` lives in the globally-imported theme.css; `.mode-confirm`
// is modes.css's, hence the explicit import.
import "./modes.css";

/** The server's own cap (`api/candidates.PICK_MAX_BYTES`, 50 MiB), repeated
 * here for a client-side pre-check. Not a second authority -- the server
 * refuses the same bytes with the same sentence -- but a 413 whose body was
 * never drained can be lost to a connection reset, and the operator would see
 * a hang rather than a refusal. */
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

const TOO_LARGE = "the upload exceeds the size cap";

interface Section {
  readonly kind: "overlays" | "fonts";
  readonly heading: string;
  readonly accept: string;
  readonly addLabel: string;
  readonly note: string;
}

const SECTIONS: readonly Section[] = [
  {
    kind: "overlays",
    heading: "Overlays",
    accept: ".png",
    addLabel: "Add an overlay PNG",
    note:
      "PNG only. A new file changes nothing until a config value names it — " +
      "artwork.<kind>.overlay_file, or a badge definition's file.",
  },
  {
    kind: "fonts",
    heading: "Fonts",
    accept: ".ttf,.otf",
    addLabel: "Add a font face",
    note:
      "TrueType or OpenType. A new face changes nothing until a config value " +
      "names it — a text block's font, or a badge definition's font.",
  },
];

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

export function Files() {
  const [files, setFiles] = useState<Record<string, readonly AssetFile[]> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [armed, setArmed] = useState<{ kind: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);

  // A response can land after the page has gone, and the same guard has to
  // cover the effect, the delete handler and the upload handler -- which a
  // per-effect `cancelled` local cannot reach. Failures.tsx's ref idiom.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      // Sequential rather than Promise.all: two small reads, and a sequence
      // makes the test's mock ordering the same as the runtime's.
      const overlays = await apiFetch<AssetFilesResponse>("/api/files/overlays");
      const fonts = await apiFetch<AssetFilesResponse>("/api/files/fonts");
      if (!live.current) return;
      setFiles({ overlays: overlays.files, fonts: fonts.files });
      setError(null);
    } catch (caught) {
      if (!live.current) return;
      setError((caught as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function remove(kind: string, name: string) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/files/${kind}/${encodeURIComponent(name)}`, { method: "DELETE" });
      // Re-read rather than dropping the row locally: the server is the
      // authority on what is in that directory now.
      await load();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBusy(false);
    }
  }

  async function upload(kind: string, file: File) {
    setError(null);
    if (file.size > MAX_UPLOAD_BYTES) {
      setError(TOO_LARGE);
      return;
    }
    setBusy(true);
    const body = new FormData();
    body.append("file", file, file.name);
    try {
      // No Content-Type header: `apiFetch`'s FormData branch leaves it to the
      // browser, which is the only writer that knows the boundary.
      await apiFetch(`/api/files/${kind}`, { method: "POST", body });
      await load();
    } catch (caught) {
      if (live.current) setError((caught as Error).message);
    } finally {
      if (live.current) setBusy(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Files</h1>
      </div>
      <p className="muted">
        The overlay images and font faces this service reads. A file is listed by name only;
        deleting one the running configuration still names is refused, and so is replacing a
        file in place — delete it first, then upload, so the re-render you get is the one you
        asked for.
      </p>
      {error !== null && (
        <p className="page-error" role="alert">
          {error}
        </p>
      )}
      {files === null ? (
        <p className="muted">Loading…</p>
      ) : (
        SECTIONS.map((section) => (
          <section key={section.kind} aria-label={section.heading} className="panel">
            <h2>{section.heading}</h2>
            <p className="muted">{section.note}</p>
            <label>
              {section.addLabel}
              <input
                type="file"
                accept={section.accept}
                aria-label={section.addLabel}
                disabled={busy}
                onChange={(event) => {
                  const chosen = event.target.files?.[0];
                  // Cleared so choosing the same file twice fires again --
                  // after a refusal the operator's next act is usually to
                  // retry the identical file.
                  event.target.value = "";
                  if (chosen) void upload(section.kind, chosen);
                }}
              />
            </label>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Size</th>
                    <th>Named by</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(files[section.kind] ?? []).map((file) => {
                    const locked = file.protected || file.referenced_by.length > 0;
                    const isArmed =
                      armed?.kind === section.kind && armed.name === file.name;
                    return (
                      <tr key={file.name}>
                        <td>{file.name}</td>
                        <td>{formatSize(file.size)}</td>
                        <td>
                          {file.protected
                            ? "ships with this service"
                            : file.referenced_by.join(", ") || "—"}
                        </td>
                        <td>
                          {locked ? null : isArmed ? (
                            <>
                              <span className="mode-confirm" role="alert">
                                {`Delete ${file.name}? Nothing names it today, so no ` +
                                  "artwork changes until you upload a replacement."}
                              </span>
                              <button
                                type="button"
                                className="primary"
                                autoFocus
                                disabled={busy}
                                onClick={() => {
                                  setArmed(null);
                                  void remove(section.kind, file.name);
                                }}
                              >
                                Confirm delete
                              </button>
                              <button type="button" onClick={() => setArmed(null)}>
                                Cancel
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                setArmed({ kind: section.kind, name: file.name })
                              }
                            >
                              Delete
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}
    </>
  );
}
