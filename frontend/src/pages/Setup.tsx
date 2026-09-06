import { useCallback, useEffect, useState, type FormEvent } from "react";

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
    // Progress is only readable with the setup token, so a page that already
    // holds one (the operator just set the password) picks up where it left
    // off, and one that does not -- including a reloaded tab, since the token
    // lives only in memory -- simply shows the password pane again.
    refresh().catch(() => setProgress(null));
  }, [refresh]);

  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true);
      setError(null);
      try {
        await action();
        await refresh();
      } catch (caught) {
        setError(setupErrorMessage(caught));
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
        <PasswordPane busy={busy} onSubmit={(value) => run(() => submitMasterPassword(value))} />
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
  onSubmit,
}: {
  id: string;
  label: string;
  type: string;
  hint: string;
  action: string;
  busy: boolean;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(value);
    // Cleared rather than kept: nothing the wizard collects is ever displayed
    // back, including by the field it was typed into.
    setValue("");
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
        value={value}
        onChange={(event) => setValue(event.target.value)}
      />
      <p>{hint}</p>
      <button className="primary" type="submit" disabled={busy || value === ""}>
        {action}
      </button>
    </form>
  );
}

function PasswordPane({ busy, onSubmit }: { busy: boolean; onSubmit: (v: string) => void }) {
  return (
    <>
      <OneFieldPane
        id="setup-password"
        label="Master password"
        type="password"
        hint="At least 12 characters. This becomes the Web UI's admin password."
        action="Continue"
        busy={busy}
        onSubmit={onSubmit}
      />
      <p data-testid="password-reload-note">
        Reloading this page loses any progress past this step, by design: the setup token lives
        only in this tab's memory and nowhere else. Prove the master password again to pick up a
        fresh one.
      </p>
    </>
  );
}

function DatabasePane({ busy, onSubmit }: { busy: boolean; onSubmit: (v: string) => void }) {
  return (
    <OneFieldPane
      id="setup-database"
      label="Database URL"
      type="text"
      hint="postgresql+asyncpg://user:password@host:5432/database — it is tried before it is saved."
      action="Test and continue"
      busy={busy}
      onSubmit={onSubmit}
    />
  );
}

function ConfigPane({ busy, onSubmit }: { busy: boolean; onSubmit: (v: string) => void }) {
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

function ProvidersPane({
  busy,
  progress,
  onSubmit,
}: {
  busy: boolean;
  progress: SetupProgress;
  onSubmit: (values: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(values);
    setValues({});
  }

  return (
    <form onSubmit={submit}>
      <p>
        Required names are marked. A stored credential is shown as stored and never
        displayed; leave a field blank to keep what is already there.
      </p>
      {Object.entries(progress.providers ?? {}).map(([name, held]) => (
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
      ))}
      <button className="primary" type="submit" disabled={busy}>
        Save and continue
      </button>
    </form>
  );
}

function WebhookSecret({ value }: { value: string }) {
  return (
    <div data-testid="webhook-secret">
      <p>Webhook secret — paste this into Sonarr and Radarr&apos;s webhook settings now:</p>
      <code data-testid="webhook-secret-value">{value}</code>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard?.writeText(value).catch(() => undefined);
        }}
      >
        Copy
      </button>
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
