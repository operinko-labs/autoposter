import { useState, type FormEvent, type ReactNode } from "react";

import { checkSystem, type CheckResult } from "../api/setup";
import { setupErrorMessage } from "./Setup";

/** One system's collapsible panel.
 *
 * Open/closed is page state and nothing else: never sent to the server, never
 * persisted, so there is no third place for "what has the operator done" to
 * live. The default follows the server's own split -- a system whose
 * credential the deployment cannot boot without opens, one whose credential is
 * already stored collapses with a Stored pill saying so, and everything else
 * collapses. A fresh deployment otherwise renders ten expanded forms and a
 * Plex sign-in flow in one column.
 */
export function SetupAccordion({
  system,
  label,
  credential,
  required,
  held,
  needsAddress,
  defaultOpen = false,
  children,
  onSave,
}: {
  /** The check endpoint's allowlist key. Never rendered. */
  system: string;
  label: string;
  /** The environment NAME this credential is known by. */
  credential: string;
  required: boolean;
  /** `"***REDACTED***"` when the deployment holds it, `null` when not. Never a
   * value -- the server does not serve one. */
  held: string | null;
  /** Whether this system's address is operator-supplied (Plex, Radarr, Sonarr,
   * Tracearr) or built in. */
  needsAddress: boolean;
  /** Open this one whatever the split below says, for a step whose caller
   * knows something this component cannot: the media-server step opens its
   * Plex card on a deployment where NOTHING is configured yet, because that
   * pane would otherwise be two closed cards and a disabled Continue where the
   * Plex-only operator used to land on the sign-in already expanded. Not
   * `required`, which would badge a card the deployment may never need. */
  defaultOpen?: boolean;
  /** The Plex accordion's extra body; absent elsewhere.
   *
   * A render prop over this form's own two fields rather than a plain node,
   * and both directions are needed. `setAddress`, because the Plex pane picks
   * the server address from plex.tv and `address` below is the only thing the
   * Check button reads: a pane that could not write into it would leave a
   * picked server checked against an empty string. `address` and `credential`,
   * because the pane's MANUAL arrival -- a typed address and a pasted token,
   * for a deployment that cannot complete a plex.tv sign-in -- reads exactly
   * these two fields. A second pair inside the pane would be two places for
   * one answer, and the Check button would read the wrong one. */
  children?: (fields: {
    address: string;
    credential: string;
    setAddress: (value: string) => void;
  }) => ReactNode;
  onSave: (credential: string, value: string) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(defaultOpen || (required && held === null));
  const [value, setValue] = useState("");
  const [address, setAddress] = useState("");
  // The credential's own label ("Plex token") names what is typed below; the
  // address field names where it is typed AT, and "token" does not belong in
  // an address -- "Plex token address" read as if the address itself were a
  // token. Only a label that ends with " token" is trimmed, so every other
  // system's label (none of which do) composes exactly as before.
  const addressLabel = label.endsWith(" token") ? label.slice(0, -" token".length) : label;
  const [result, setResult] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    try {
      // Both inputs of this form, read the same way. Sending only the address
      // checked whatever credential was last SAVED, so an operator who pasted
      // a fresh key and pressed Check was told the key was refused -- about a
      // key that is correct. Empty means keep, so an untouched field checks
      // the credential the deployment holds.
      setResult(
        await checkSystem(system, needsAddress ? address : null, value === "" ? null : value),
      );
    } catch (caught) {
      setResult({ ok: false, detail: setupErrorMessage(caught) });
    } finally {
      setBusy(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    // Cleared only once the server accepted it: a refusal must not force a
    // pasted credential to be found again.
    if (await onSave(credential, value)) setValue("");
  }

  return (
    <section className="setup-accordion">
      <button
        aria-expanded={open}
        className="setup-accordion-head"
        type="button"
        onClick={() => setOpen((previous) => !previous)}
      >
        <span className="setup-field-label">{label}</span>
        {required && <span className="setup-badge">Required</span>}
        <span
          className={`setup-pill${held === null ? "" : " ok"}`}
          data-testid={`held-${credential}`}
        >
          {held === null ? "Not set" : "Stored"}
        </span>
        {result !== null && (
          <span
            className={`setup-pill${result.ok ? " ok" : ""}`}
            data-testid={`check-result-${system}`}
          >
            {result.detail}
          </span>
        )}
      </button>
      {open && (
        <form
          className="setup-accordion-body"
          data-testid={`accordion-body-${system}`}
          onSubmit={save}
        >
          {needsAddress && (
            <div className="setup-field">
              <label className="setup-field-label" htmlFor={`${system}-address`}>
                {addressLabel} address
              </label>
              <input
                className="setup-input"
                id={`${system}-address`}
                aria-label={`${addressLabel} address`}
                type="text"
                autoComplete="off"
                value={address}
                onChange={(event) => setAddress(event.target.value)}
              />
            </div>
          )}
          <div className="setup-field">
            <label className="setup-field-label" htmlFor={credential}>
              {credential}
            </label>
            <input
              className="setup-input"
              id={credential}
              aria-label={credential}
              type="password"
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </div>
          {children?.({ address, credential: value, setAddress })}
          <div className="setup-field-head">
            <button type="submit" disabled={busy}>
              Save
            </button>
            <button type="button" disabled={busy} onClick={check}>
              Check connection
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
