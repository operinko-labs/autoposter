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
  /** The Plex accordion's extra body (Task 3); absent elsewhere. */
  children?: ReactNode;
  onSave: (credential: string, value: string) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(required && held === null);
  const [value, setValue] = useState("");
  const [address, setAddress] = useState("");
  const [result, setResult] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    try {
      setResult(await checkSystem(system, needsAddress ? address : null));
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
                {label} address
              </label>
              <input
                className="setup-input"
                id={`${system}-address`}
                aria-label={`${label} address`}
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
          {children}
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
