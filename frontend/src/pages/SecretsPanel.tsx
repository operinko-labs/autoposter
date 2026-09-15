/** The System tab's secrets accordion: every name, its source, one control.
 *
 * The typed value lives in component state for exactly as long as the request
 * does and is dropped when the request ends, won or lost. There is nothing to
 * re-render it from -- the server answers a source, never a value -- which is
 * what makes "never shows a secret" a property of the data rather than a
 * discipline this component has to keep.
 *
 * The generated credential is listed like every other name, but its row is
 * followed by the rotation panel rather than by a field: it is minted here and
 * never typed, so there is nothing to set, and the one thing an operator can
 * do to it is the thing that panel does.
 */
import { useCallback, useEffect, useState } from "react";

import { refusalMessage } from "../api/overrides";
import {
  clearSecret,
  fetchSecrets,
  setSecret,
  type SecretRow,
} from "../api/secrets";
import { SettingsAccordion } from "./SettingsAccordion";
import { WebhookSecretPanel } from "./WebhookSecretPanel";

/** The two credentials that live on their server's card instead: they belong
 * to one server, and a page that set them apart from the server they
 * authenticate to would be two places to look for one answer. */
export const SERVER_CREDENTIAL_NAMES = [
  "AUTOPOSTER_PLEX_TOKEN",
  "AUTOPOSTER_JELLYFIN_APIKEY",
];

/** The served description for one environment NAME, or undefined.
 *
 * The map the server sends is keyed by the setting's dotted path
 * (`secrets.tmdb_token`) and this accordion is keyed by the environment
 * variable, so the lower-cased suffix is the lookup. A name the two spell
 * differently -- the pair above, whose field is `jellyfin_api_key` -- is on
 * its server's card rather than here; anything else that misses renders no
 * title at all, because an empty hover is worse than none.
 */
function describedBy(
  descriptions: Record<string, string>,
  name: string,
): string | undefined {
  const path = `secrets.${name.replace(/^AUTOPOSTER_/, "").toLowerCase()}`;
  const text = descriptions[path];
  return text === undefined || text === "" ? undefined : text;
}

export function SecretsPanel({
  descriptions = {},
  onChanged,
}: {
  /** The served `field_descriptions`, so a row can say what its secret is
   * for. Absent while the configuration is still loading, and this panel
   * renders without it -- the names and their sources are its own fetch. */
  descriptions?: Record<string, string>;
  /** Re-read `/api/config` and re-adopt. Called after every write: a stored
   * secret changes what the restart list holds and what the rest of the page
   * says about its sources, and neither is re-read by this panel's own
   * fetch. */
  onChanged?: () => Promise<void> | void;
}) {
  const [rows, setRows] = useState<SecretRow[] | null>(null);
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setRows(await fetchSecrets());
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchSecrets()
      .then((found) => {
        if (!cancelled) setRows(found);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(refusalMessage(caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function write(name: string, action: "set" | "clear") {
    setBusy(name);
    setError(null);
    setNote(null);
    try {
      if (action === "set") {
        await setSecret(name, typed[name] ?? "");
        setNote(`Stored ${name}. It is not shown again.`);
      } else {
        const cleared = await clearSecret(name);
        setNote(clearedNote(cleared.source, cleared.restart_required === true, name));
      }
      await load();
      await onChanged?.();
    } catch (caught) {
      // The server's own sentence, whatever shape it arrived in.
      setError(refusalMessage(caught));
    } finally {
      // Dropped whether the write landed or was refused: a refused value is
      // one the store would not take, so re-typing it is the correction, and
      // a field left holding a secret is one reload away from being the only
      // copy of it on the screen.
      setTyped((current) => ({ ...current, [name]: "" }));
      setBusy(null);
    }
  }

  const listed = (rows ?? []).filter(
    (row) => !SERVER_CREDENTIAL_NAMES.includes(row.name),
  );

  return (
    <SettingsAccordion title="Secrets" open={open} onToggle={() => setOpen(!open)}>
      <p className="muted config-note">
        Stored secrets are encrypted with a key kept in this deployment&apos;s
        state directory and are never shown again. A stored value wins over one
        in the state file or the environment; clearing it hands the secret back
        to whichever of those still answers. Each server&apos;s own credential
        is on its card, under Servers.
      </p>
      {error !== null && <p className="page-error">{error}</p>}
      {note !== null && <p className="config-saved">{note}</p>}
      {rows === null && <p className="muted">Loading…</p>}
      {listed.map((row) => (
        <div key={row.name}>
          <div className="config-row">
            <span className="config-key" title={describedBy(descriptions, row.name)}>
              {row.name}
            </span>
            <span className="config-value">
              <span className="config-pill">{row.source}</span>
              {row.generated ? (
                <span className="muted">
                  This secret is generated by this service, never typed —
                  rotate it below.
                </span>
              ) : (
                <>
                  <input
                    type="password"
                    aria-label={row.name}
                    value={typed[row.name] ?? ""}
                    onChange={(event) =>
                      setTyped((current) => ({
                        ...current,
                        [row.name]: event.target.value,
                      }))
                    }
                  />
                  <button
                    type="button"
                    aria-label={`${row.source === "stored" ? "Replace" : "Set"} ${row.name}`}
                    disabled={busy !== null}
                    onClick={() => void write(row.name, "set")}
                  >
                    {row.source === "stored" ? "Replace" : "Set"}
                  </button>
                  {row.source === "stored" && (
                    <button
                      type="button"
                      aria-label={`Clear ${row.name}`}
                      disabled={busy !== null}
                      onClick={() => void write(row.name, "clear")}
                    >
                      Clear
                    </button>
                  )}
                </>
              )}
            </span>
          </div>
          {row.generated && <WebhookSecretPanel />}
        </div>
      ))}
    </SettingsAccordion>
  );
}

/** What a clear leaves behind, in the server's terms.
 *
 * `restart_required` is the case this sentence exists for: the layer that
 * takes over is one this process cannot read back, so the row above is
 * already accurate and the value behind it is not in force yet.
 */
function clearedNote(
  source: string,
  restartRequired: boolean,
  name: string,
): string {
  if (restartRequired) {
    return `Cleared ${name}. The value that takes over is read at the next restart.`;
  }
  if (source === "unset") {
    return `Cleared ${name}. No other source supplies it, so it is now unset.`;
  }
  return `Cleared ${name}. It now comes from the ${source}.`;
}
