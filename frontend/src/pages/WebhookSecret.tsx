/** The once-only reveal of a webhook secret, shared by the setup wizard's
 * finish pane and the Settings page's rotation panel.
 *
 * Its own module so `Settings` can use it without importing
 * `SetupFinishPane`, which imports `providerLabel` from `./Setup` and would
 * drag the whole wizard into the settings bundle. A cut-and-paste with no
 * behaviour change: `SetupFinishPane` imports it from here now.
 *
 * `setup.css` is imported here rather than relied on from `Setup.tsx`: this
 * module owns the `.setup-secret*` classes it renders, and Vite dedupes the
 * import.
 */
import { useRef, useState } from "react";

import "./setup.css";

export function WebhookSecret({ value }: { value: string }) {
  const codeRef = useRef<HTMLElement | null>(null);
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "manual">("idle");

  function selectCodeText() {
    const node = codeRef.current;
    const selection = node !== null ? window.getSelection() : null;
    if (node === null || selection === null) return;
    const range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  async function copy() {
    // `navigator.clipboard` is undefined outside a secure context -- exactly
    // the shape a plain-HTTP compose deployment runs in, which is what this
    // wizard is for -- so a missing API is one of the failure branches, never
    // a silent no-op: this value is shown exactly once, and a click that does
    // nothing reads as success to an operator who then moves on without it.
    if (navigator.clipboard === undefined) {
      selectCodeText();
      setCopyStatus("manual");
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      setCopyStatus("copied");
    } catch {
      selectCodeText();
      setCopyStatus("manual");
    }
  }

  return (
    <div className="setup-secret" data-testid="webhook-secret">
      <p className="setup-lead">
        Webhook secret — paste this into Sonarr and Radarr&apos;s webhook settings now:
      </p>
      <code className="mono setup-secret-value" ref={codeRef} data-testid="webhook-secret-value">
        {value}
      </code>
      <div className="setup-field-head">
        <button type="button" onClick={copy}>
          Copy
        </button>
        {copyStatus === "copied" && (
          <span className="setup-hint" data-testid="webhook-copy-status">
            Copied
          </span>
        )}
        {copyStatus === "manual" && (
          <span className="setup-hint" data-testid="webhook-copy-status">
            Select and copy the value above.
          </span>
        )}
      </div>
      <p className="setup-hint">This will not be shown again.</p>
    </div>
  );
}
