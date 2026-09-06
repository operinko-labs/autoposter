import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  fetchSetupProgress,
  fetchSetupState,
  finishSetup,
  submitDatabaseUrl,
  submitMasterPassword,
  submitPlexUrl,
  submitProviderKeys,
  type SetupProgress,
} from "../api/setup";
import "./login.css";

/** The message shown for a failed wizard call.
 *
 * The server's `detail` is always a fixed sentence or an exception class name
 * -- no value the wizard was given is ever served back (roadmap row 213) --
 * so it is rendered verbatim, which is the whole point of it being fixed.
 */
export function setupErrorMessage(caught: unknown): string {
  if (!(caught instanceof ApiError)) {
    return "Could not reach the server. Check that it is running and try again.";
  }
  if (typeof caught.detail === "string") return caught.detail;
  return `The server returned an error (${caught.status}). Try again.`;
}

export function Setup() {
  const [progress, setProgress] = useState<SetupProgress | null>(null);
  // `/api/setup/state` is the one open route, readable before any token
  // exists, and its `password_set` is what tells the first pane whether there
  // is a password to SET or one to PROVE -- the same distinction the reload
  // path creates. Defaults to "set": the closed, first-visit answer.
  const [passwordSet, setPasswordSet] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restarting, setRestarting] = useState(false);
  // Set once, by the one call that ever generates it (facts Amendment 5), and
  // never cleared by a later call: a later provider submit answers `null` for
  // this field (the secret already exists), and that must not make a value
  // shown a moment ago disappear as if it had been withdrawn.
  const [webhookSecret, setWebhookSecret] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setProgress(await fetchSetupProgress());
  }, []);

  useEffect(() => {
    fetchSetupState()
      .then((state) => setPasswordSet(state.password_set ?? false))
      .catch(() => setPasswordSet(false));
  }, []);

  useEffect(() => {
    // Progress is only readable with the setup token, so a page that already
    // holds one (the operator just set the password) picks up where it left
    // off, and one that does not -- including a reloaded tab, since the token
    // lives only in memory -- simply shows the password pane again.
    refresh().catch(() => setProgress(null));
  }, [refresh]);

  const run = useCallback(
    async (action: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        await action();
        await refresh();
        return true;
      } catch (caught) {
        setError(setupErrorMessage(caught));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  if (restarting) return <Restarting />;
  if (progress === null) {
    return (
      <Shell error={error}>
        <PasswordPane
          busy={busy}
          passwordSet={passwordSet}
          onSubmit={(value) => run(() => submitMasterPassword(value))}
        />
      </Shell>
    );
  }

  // Every unfinished step is offered together rather than one at a time: the
  // server, not step order, is what says which credentials are still needed,
  // and the provider step in particular has no "done" moment of its own --
  // several credentials are optional forever -- so it stays available
  // alongside whatever else is unfinished.
  const readyToFinish =
    progress.database && progress.required.length === 0 && progress.config_source !== null;

  return (
    <Shell error={error}>
      {!progress.database && (
        <DatabasePane busy={busy} onSubmit={(url) => run(() => submitDatabaseUrl(url))} />
      )}
      <ProvidersPane
        busy={busy}
        progress={progress}
        onSubmit={(values) =>
          run(async () => {
            const result = await submitProviderKeys(values);
            if (result.webhook_secret !== null) setWebhookSecret(result.webhook_secret);
          })
        }
      />
      {webhookSecret !== null && <WebhookSecret value={webhookSecret} />}
      {progress.config_source === null ? (
        <ConfigPane busy={busy} onSubmit={(url) => run(() => submitPlexUrl(url))} />
      ) : (
        // Amendment 6: a document that already resolves (an env-set path, a
        // mounted ConfigMap, compose's bind-mounted example) is never offered
        // this step again -- writing beside it would produce a file the next
        // boot does not read, with the operator's Plex URL landing in it.
        <p data-testid="config-satisfied">
          Configuration already provided ({progress.config_source}).
        </p>
      )}
      {readyToFinish && (
        <FinishPane
          busy={busy}
          onSubmit={() =>
            run(async () => {
              await finishSetup();
              setRestarting(true);
            })
          }
        />
      )}
    </Shell>
  );
}

function Shell({ error, children }: { error: string | null; children: React.ReactNode }) {
  return (
    <div className="login-screen">
      <div className="login-box">
        <h1 className="login-title">Set up Autoposter</h1>
        {children}
        {error !== null && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

function Restarting() {
  // The server replaces its own process image the moment this response is
  // written, so the next request fails until the real application is up. The
  // probe is the only thing that can tell us it is.
  useEffect(() => {
    const timer = window.setInterval(() => {
      fetchSetupState()
        .then((state) => {
          if (!state.setup) window.location.reload();
        })
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="login-screen">
      <div className="login-box">
        <h1 className="login-title">Starting Autoposter…</h1>
        <p>This page reloads by itself once the service is up.</p>
      </div>
    </div>
  );
}

function OneFieldPane({
  id,
  label,
  type,
  hint,
  action,
  busy,
  autoComplete,
  clearAlways,
  onSubmit,
}: {
  id: string;
  label: string;
  type: string;
  hint: string;
  action: string;
  busy: boolean;
  autoComplete?: string;
  // The password pane's own exception (see PasswordPane): a wrong-password
  // refusal clearing the field is correct, and it is not a paste the operator
  // has to reconstruct.
  clearAlways?: boolean;
  onSubmit: (value: string) => Promise<boolean>;
}) {
  const [value, setValue] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    const accepted = await onSubmit(value);
    // Cleared only once the server has accepted the value (or for the
    // password pane, always): a database or config refusal -- a typo in the
    // DSN, a Plex URL rejected -- must not force the whole value to be
    // retyped or re-pasted from wherever it came from.
    if (accepted || clearAlways) setValue("");
  }

  return (
    <form onSubmit={submit}>
      <label className="login-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        aria-label={label}
        type={type}
        autoComplete={autoComplete}
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      {hint !== "" && <p>{hint}</p>}
      <button className="primary" type="submit" disabled={busy || value === ""}>
        {action}
      </button>
    </form>
  );
}

function PasswordPane({
  busy,
  passwordSet,
  onSubmit,
}: {
  busy: boolean;
  passwordSet: boolean;
  onSubmit: (v: string) => Promise<boolean>;
}) {
  return (
    <>
      <OneFieldPane
        id="setup-password"
        label={passwordSet ? "Prove the master password" : "Set the master password"}
        type="password"
        hint={passwordSet ? "" : "At least 12 characters. This becomes the Web UI's admin password."}
        action="Continue"
        busy={busy}
        autoComplete="new-password"
        clearAlways
        onSubmit={onSubmit}
      />
      {passwordSet && (
        <p data-testid="password-reload-note">
          Reloading this page loses any progress past this step, by design: the setup token lives
          only in this tab's memory and nowhere else. Prove the master password again to pick up a
          fresh one.
        </p>
      )}
    </>
  );
}

function DatabasePane({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (v: string) => Promise<boolean>;
}) {
  return (
    <OneFieldPane
      id="setup-database"
      label="Database URL"
      type="text"
      hint="postgresql+asyncpg://user:password@host:5432/database — it is tried before it is saved."
      action="Test and continue"
      busy={busy}
      autoComplete="off"
      onSubmit={onSubmit}
    />
  );
}

function ConfigPane({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (v: string) => Promise<boolean>;
}) {
  return (
    <OneFieldPane
      id="setup-plex-url"
      label="Plex server URL"
      type="text"
      hint="http://plex:32400 — everything else starts from the shipped defaults and is editable in Settings."
      action="Write the configuration"
      busy={busy}
      onSubmit={onSubmit}
    />
  );
}

// The one provider credential this deployment mints itself rather than
// collects (backend's own name for it: setup.py's `_GENERATED_SECRET`). The
// server refuses it outright on every submit (`WEBHOOK_SECRET_IS_GENERATED`),
// so it is not a field here -- an input the server can never accept is not a
// credential to paste, it is a status to read.
const GENERATED_SECRET_NAME = "AUTOPOSTER_WEBHOOK_SECRET";

function ProvidersPane({
  busy,
  progress,
  onSubmit,
}: {
  busy: boolean;
  progress: SetupProgress;
  onSubmit: (values: Record<string, string>) => Promise<boolean>;
}) {
  const [values, setValues] = useState<Record<string, string>>({});

  async function submit(event: FormEvent) {
    event.preventDefault();
    // Cleared only once the server has accepted them: a refusal -- an unknown
    // name, a one-line-value violation, the generated secret rejected above,
    // a 429 from the limiter, a 503 from a state directory that will not take
    // the write -- must not force every other pasted credential in this
    // submit to be retyped.
    if (await onSubmit(values)) setValues({});
  }

  return (
    <form onSubmit={submit}>
      <p>
        Required names are marked. A stored credential is shown as stored and never
        displayed; leave a field blank to keep what is already there.
      </p>
      {Object.entries(progress.providers).map(([name, held]) =>
        name === GENERATED_SECRET_NAME ? (
          <div key={name}>
            <span className="login-label">{name}</span>
            <span data-testid={`held-${name}`}>{held === null ? "Not set" : "Stored"}</span>
            <p>
              Generated for you when you save this form, and shown once, immediately after.
              There is nothing to paste here.
            </p>
          </div>
        ) : (
          <div key={name}>
            <label className="login-label" htmlFor={name}>
              {name}
              {progress.required.includes(name) ? " (required)" : ""}
            </label>
            <span data-testid={`held-${name}`}>{held === null ? "Not set" : "Stored"}</span>
            <input
              id={name}
              aria-label={name}
              type="password"
              value={values[name] ?? ""}
              onChange={(event) =>
                setValues((previous) => ({ ...previous, [name]: event.target.value }))
              }
            />
          </div>
        ),
      )}
      <button className="primary" type="submit" disabled={busy}>
        Save and continue
      </button>
    </form>
  );
}

function WebhookSecret({ value }: { value: string }) {
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
    <div data-testid="webhook-secret">
      <p>Webhook secret — paste this into Sonarr and Radarr&apos;s webhook settings now:</p>
      <code ref={codeRef} data-testid="webhook-secret-value" style={{ wordBreak: "break-all" }}>
        {value}
      </code>
      <button type="button" onClick={copy}>
        Copy
      </button>
      {copyStatus === "copied" && <p data-testid="webhook-copy-status">Copied</p>}
      {copyStatus === "manual" && (
        <p data-testid="webhook-copy-status">Select and copy the value above.</p>
      )}
      <p>This will not be shown again.</p>
    </div>
  );
}

function FinishPane({ busy, onSubmit }: { busy: boolean; onSubmit: () => void }) {
  return (
    <div>
      <p>Everything Autoposter needs is set. Starting it restarts this service once.</p>
      <button className="primary" type="button" disabled={busy} onClick={onSubmit}>
        Start autoposter
      </button>
    </div>
  );
}
