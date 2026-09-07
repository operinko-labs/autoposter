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
import "./setup.css";

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

/** The wizard's five steps, in the order the server finishes them. Nothing
 * here decides what is rendered -- the panes below are still chosen by what
 * `/api/setup/progress` says is unfinished -- it is the operator's map of how
 * far along they are. */
const STEPS = ["Password", "Database", "Providers", "Configuration", "Start"] as const;

/** Which of the five the operator is on, read off the progress the page
 * already holds. Derived, never stored: a second source of truth for "where
 * am I" is a second thing that can fall out of step with the server. */
function stepNumber(progress: SetupProgress | null): number {
  if (progress === null) return 1;
  if (!progress.database) return 2;
  if (progress.required.length > 0) return 3;
  if (progress.config_source === null) return 4;
  return 5;
}

/** A human name for each credential the provider step collects.
 *
 * The backend's `_PROVIDER_ENV` (api/setup.py) is the list and the server
 * sends it; this is only how each name is spelled to a person. A name that is
 * not here -- a provider secret added to the model after this map was written
 * -- falls back to the environment name, which is what every field showed
 * before. The raw name is rendered under the label either way: operators know
 * these credentials by it.
 */
const PROVIDER_LABELS: Record<string, string> = {
  AUTOPOSTER_PLEX_TOKEN: "Plex token",
  AUTOPOSTER_TMDB_TOKEN: "TMDb token",
  AUTOPOSTER_TVDB_APIKEY: "TVDB API key",
  AUTOPOSTER_FANART_APIKEY: "Fanart API key",
  AUTOPOSTER_WEBHOOK_SECRET: "Webhook secret",
  AUTOPOSTER_MDBLIST_APIKEY: "MDBList API key",
  AUTOPOSTER_RADARR_APIKEY: "Radarr API key",
  AUTOPOSTER_SONARR_APIKEY: "Sonarr API key",
  AUTOPOSTER_HARBOR_TOKEN: "Harbor token",
  AUTOPOSTER_PLEX_ACCOUNT_TOKEN: "Plex account token",
  AUTOPOSTER_TRACEARR_APIKEY: "Tracearr API key",
};

function providerLabel(name: string): string {
  return PROVIDER_LABELS[name] ?? name;
}

/** The provider credentials a deployment cannot boot without: the hard half of
 * config/schema.py's `_SECRET_ENV`, less the database URL, which has its own
 * step.
 *
 * `progress.required` is NOT this list and cannot stand in for it: the server
 * answers there with the hard names still UNRESOLVED, so a credential leaves
 * it the moment it is stored. That is the right answer to "what is still
 * outstanding" -- which is what the page reads it for everywhere else -- and
 * the wrong one to "which heading does this field live under": filed by it, a
 * Plex token would move itself from Required to Optional by being saved. A
 * name the server calls required is treated as required whether or not it is
 * named here, so a hard secret added to the model after this list was written
 * is still filed correctly while it is missing.
 */
const REQUIRED_PROVIDER_NAMES = [
  "AUTOPOSTER_PLEX_TOKEN",
  "AUTOPOSTER_TMDB_TOKEN",
  "AUTOPOSTER_TVDB_APIKEY",
  "AUTOPOSTER_FANART_APIKEY",
  "AUTOPOSTER_WEBHOOK_SECRET",
];

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
      <Shell error={error} step={stepNumber(null)}>
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
    <Shell error={error} step={stepNumber(progress)}>
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
        // This is the configuration step's own status, so it is reported in
        // that step's header rather than as a loose line under the
        // credentials.
        <section className="setup-pane">
          <div className="setup-pane-head">
            <h2 className="setup-pane-title">Configuration</h2>
            <span className="setup-pill ok" data-testid="config-satisfied">
              Already provided ({progress.config_source})
            </span>
          </div>
          <p className="setup-lead">
            The document the next boot reads already resolves, so there is nothing to write here.
            Everything in it stays editable in Settings.
          </p>
        </section>
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

function Shell({
  error,
  step,
  children,
}: {
  error: string | null;
  step: number;
  children: React.ReactNode;
}) {
  return (
    <div className="setup-screen">
      <div className="setup-box">
        <h1 className="setup-title">Set up Autoposter</h1>
        <Steps current={step} />
        {children}
        {error !== null && (
          <p className="page-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

function Steps({ current }: { current: number }) {
  return (
    <ol className="setup-steps">
      {STEPS.map((label, index) => {
        const number = index + 1;
        const state = number < current ? "done" : number === current ? "current" : "todo";
        return (
          <li
            className={`setup-step ${state}`}
            key={label}
            aria-current={state === "current" ? "step" : undefined}
          >
            {/* A finished step is marked by what is in its disc as well as by
                the disc's colour, so the distinction survives a colour-blind
                reading. */}
            <span aria-hidden="true" className="setup-step-number">
              {state === "done" ? "✓" : number}
            </span>
            {label}
          </li>
        );
      })}
    </ol>
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
    <div className="setup-screen">
      <div className="setup-box">
        <h1 className="setup-title">Starting Autoposter…</h1>
        <p className="setup-lead">This page reloads by itself once the service is up.</p>
      </div>
    </div>
  );
}

function OneFieldPane({
  id,
  title,
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
  /** The step's own heading, for the panes whose field label alone does not
   * say which step this is. The password pane has none: the card's own title
   * is already its heading. */
  title?: string;
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
    <form className="setup-pane" onSubmit={submit}>
      {title !== undefined && (
        <div className="setup-pane-head">
          <h2 className="setup-pane-title">{title}</h2>
        </div>
      )}
      <div className="setup-field">
        <div className="setup-field-head">
          <label className="setup-field-label" htmlFor={id}>
            {label}
          </label>
        </div>
        <input
          className="setup-input"
          id={id}
          aria-label={label}
          type={type}
          autoComplete={autoComplete}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </div>
      {hint !== "" && <p className="setup-hint">{hint}</p>}
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
        <p className="setup-hint" data-testid="password-reload-note">
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
      title="Database"
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
      title="Configuration"
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

/** One credential's header row: its human name, whether the server calls it
 * required, whether the deployment already holds it -- and, under those, the
 * environment name operators know it by. */
function FieldHead({
  name,
  required,
  held,
  labelFor,
}: {
  name: string;
  required: boolean;
  held: string | null;
  /** The input this names, or `undefined` for the generated secret, which has
   * no input to name -- and must not look as though it has one. */
  labelFor?: string;
}) {
  const text = providerLabel(name);
  return (
    <>
      <div className="setup-field-head">
        {labelFor === undefined ? (
          <span className="setup-field-label">{text}</span>
        ) : (
          <label className="setup-field-label" htmlFor={labelFor}>
            {text}
          </label>
        )}
        {required && <span className="setup-badge">Required</span>}
        <span className={`setup-pill${held === null ? "" : " ok"}`} data-testid={`held-${name}`}>
          {held === null ? "Not set" : "Stored"}
        </span>
      </div>
      <code className="mono setup-field-env">{name}</code>
    </>
  );
}

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

  // The server owns the list and its order; each group keeps the order it was
  // served in.
  const isRequired = (name: string) =>
    REQUIRED_PROVIDER_NAMES.includes(name) || progress.required.includes(name);
  const entries = Object.entries(progress.providers);
  const required = entries.filter(([name]) => isRequired(name));
  const optional = entries.filter(([name]) => !isRequired(name));

  function field([name, held]: [string, string | null]) {
    return name === GENERATED_SECRET_NAME ? (
      <div className="setup-field" key={name}>
        <FieldHead held={held} name={name} required={isRequired(name)} />
        <p className="setup-hint">
          Generated for you when you save this form, and shown once, immediately after. There is
          nothing to paste here.
        </p>
      </div>
    ) : (
      <div className="setup-field" key={name}>
        <FieldHead held={held} labelFor={name} name={name} required={isRequired(name)} />
        <input
          className="setup-input"
          id={name}
          aria-label={name}
          type="password"
          value={values[name] ?? ""}
          onChange={(event) =>
            setValues((previous) => ({ ...previous, [name]: event.target.value }))
          }
        />
      </div>
    );
  }

  return (
    <form className="setup-pane setup-form" onSubmit={submit}>
      <div className="setup-pane-head">
        <h2 className="setup-pane-title">Provider credentials</h2>
      </div>
      <p className="setup-lead">
        A stored credential is shown as stored and never displayed; leave a field blank to keep
        what is already there.
      </p>
      {required.length > 0 && (
        <section className="setup-group">
          <h3 className="setup-group-title">Required</h3>
          <p className="setup-hint">Autoposter does not leave setup until these are held.</p>
          {required.map(field)}
        </section>
      )}
      {optional.length > 0 && (
        <section className="setup-group">
          <h3 className="setup-group-title">Optional</h3>
          <p className="setup-hint">
            Leave blank whatever this deployment does not use. Each one can be added later.
          </p>
          {optional.map(field)}
        </section>
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

function FinishPane({ busy, onSubmit }: { busy: boolean; onSubmit: () => void }) {
  return (
    <section className="setup-pane">
      <div className="setup-pane-head">
        <h2 className="setup-pane-title">Ready</h2>
      </div>
      <p className="setup-lead">
        Everything Autoposter needs is set. Starting it restarts this service once.
      </p>
      <button className="primary" type="button" disabled={busy} onClick={onSubmit}>
        Start autoposter
      </button>
    </section>
  );
}
