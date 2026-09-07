import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  fetchSetupProgress,
  fetchSetupState,
  fetchWebhookSecret,
  finishSetup,
  registerArrWebhook,
  submitDatabaseUrl,
  submitMasterPassword,
  submitPlexSelection,
  submitProviderKeys,
  submitPublicUrl,
  type ArrRegistration,
  type SetupProgress,
} from "../api/setup";
import { SetupAccordion } from "./SetupAccordion";
import { SetupFinishPane } from "./SetupFinishPane";
import { SetupPlexPane } from "./SetupPlexPane";
import {
  canNavigate,
  farthestStep,
  STEP_LABELS,
  stepAfter,
  visibleSteps,
  type StepId,
} from "./setupSteps";
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

/** Exported for the finish pane's "left for later" list, which files every
 * unset credential by the same human name the systems step gave it. */
export function providerLabel(name: string): string {
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

/** Environment NAME -> the check endpoint's allowlist key. A credential with
 * no entry here gets no check button, which is the honest rendering for one
 * the wizard cannot probe -- the generated webhook secret is the only such
 * name, and it stays a status rather than a field. */
const SYSTEM_FOR_CREDENTIAL: Record<string, string> = {
  AUTOPOSTER_PLEX_TOKEN: "plex",
  AUTOPOSTER_PLEX_ACCOUNT_TOKEN: "plex_account",
  AUTOPOSTER_TMDB_TOKEN: "tmdb",
  AUTOPOSTER_TVDB_APIKEY: "tvdb",
  AUTOPOSTER_FANART_APIKEY: "fanart",
  AUTOPOSTER_MDBLIST_APIKEY: "mdblist",
  AUTOPOSTER_RADARR_APIKEY: "radarr",
  AUTOPOSTER_SONARR_APIKEY: "sonarr",
  AUTOPOSTER_HARBOR_TOKEN: "harbor",
  AUTOPOSTER_TRACEARR_APIKEY: "tracearr",
};

/** The four whose address the operator supplies -- the check endpoint's own
 * split, and the only four with an SSRF surface at all. */
const SYSTEMS_WITH_AN_ADDRESS = new Set(["plex", "radarr", "sonarr", "tracearr"]);

export function Setup() {
  const [progress, setProgress] = useState<SetupProgress | null>(null);
  const [passwordSet, setPasswordSet] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [webhookSecret, setWebhookSecret] = useState<string | null>(null);
  // What each *arr answered, keyed by service. Page state and deliberately not
  // a server surface: a registration is an EVENT, the server keeps no record of
  // one, and the finish page reports what happened during THIS wizard.
  const [registrations, setRegistrations] = useState<Record<string, ArrRegistration>>({});
  // The address the URL step submitted. Held here because /progress reports
  // `public_url` as PRESENCE and never as a value -- it is a presence surface
  // for every line it serves -- and the finish page has to show the operator
  // the callback URL that was registered so they can check it against what
  // their reverse proxy actually serves.
  const [publicUrl, setPublicUrl] = useState<string | null>(null);
  // Which pane the operator is on. Page state, deliberately: the server owns
  // how far forward this may go (`farthestStep`) and nothing else about it.
  // Back is a `setCurrent` and no request at all -- see setupSteps.ts.
  const [current, setCurrent] = useState<StepId>("password");

  const refresh = useCallback(async () => {
    setProgress(await fetchSetupProgress());
  }, []);

  useEffect(() => {
    fetchSetupState()
      .then((state) => setPasswordSet(state.password_set ?? false))
      .catch(() => setPasswordSet(false));
  }, []);

  useEffect(() => {
    // A failed probe leaves `progress` at the null it starts as, which is the
    // password pane. Setting it back to null here instead would let this
    // fetch -- issued at mount, before any token exists, so it 401s on every
    // real deployment -- throw away a progress the server DID answer if it
    // settles after a submit has already succeeded: the wizard would bounce
    // back to step 1 mid-flow.
    refresh().catch(() => undefined);
  }, [refresh]);

  /** Run a step's submit, and advance only if the server accepted it.
   *
   * `from` is the step being submitted, never the step to land on: where to
   * land is read from the progress this call just fetched, because that is
   * the only thing that knows what the submit changed. The password step is
   * the case that proves it -- before it, the wizard's whole order is
   * `["password"]`.
   */
  const run = useCallback(
    async (action: () => Promise<unknown>, from?: StepId): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        await action();
        const next = await fetchSetupProgress();
        setProgress(next);
        if (from !== undefined) setCurrent(stepAfter(from, next));
        return true;
      } catch (caught) {
        setError(setupErrorMessage(caught));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const order = visibleSteps(progress);
  // A pane the server has since said is unreachable (a reload dropped the
  // token, so progress went back to null) must not stay on screen.
  const pane: StepId = canNavigate(current, progress) && order.includes(current)
    ? current
    : "password";
  const back = order[order.indexOf(pane) - 1];

  // The one value this API serves, asked for on the one pane that shows it and
  // only while the page does not already hold it: the server answers it ONCE
  // and `null` thereafter, so stepping back from here and forward again
  // re-renders what was already served rather than spending a second serve.
  useEffect(() => {
    if (pane !== "finish" || webhookSecret !== null) return;
    fetchWebhookSecret()
      .then((body) => setWebhookSecret((held) => held ?? body.webhook_secret))
      .catch(() => undefined);
  }, [pane, webhookSecret]);

  if (restarting) return <Restarting />;

  return (
    <Shell
      error={error}
      steps={order}
      current={pane}
      onBack={back === undefined ? undefined : () => setCurrent(back)}
    >
      {pane === "password" && (
        <PasswordPane
          busy={busy}
          passwordSet={passwordSet}
          onSubmit={(value) => run(() => submitMasterPassword(value), "password")}
        />
      )}
      {pane === "url" && (
        <PublicUrlPane
          busy={busy}
          stored={progress?.public_url ?? false}
          onSubmit={async (value) => {
            const accepted = await run(() => submitPublicUrl(value), "url");
            // Only what the server took: the finish page shows this address as
            // the base of the registered callback, and a refused one is not
            // what any *arr was given. An empty submit -- the "keep what you
            // have" case -- leaves whatever was held, since re-typing a staged
            // value is not something the wizard can ask for.
            if (accepted && value !== "") setPublicUrl(value.trim().replace(/\/+$/, ""));
            return accepted;
          }}
        />
      )}
      {pane === "database" && (
        <DatabasePane
          busy={busy}
          stored={progress?.database ?? false}
          onSubmit={(url) => run(() => submitDatabaseUrl(url), "database")}
        />
      )}
      {pane === "systems" && progress !== null && (
        <>
          <SystemsPane
            busy={busy}
            progress={progress}
            onSave={(values) => run(() => submitProviderKeys(values))}
            onSelectPlex={(plexUrl, excluded) =>
              run(() => submitPlexSelection(plexUrl, excluded))
            }
            onRegister={async (service) => {
              // Not through `run`: that advances the step and reports a
              // failure as the page's own error, and a registration is neither
              // -- it never blocks the finish (facts C3), and its result is
              // reported per service on the last pane. The route answers 200
              // with `ok: false` for every failure it has, so the only reject
              // reachable here is the fetch itself.
              try {
                const result = await registerArrWebhook(service);
                setRegistrations((previous) => ({ ...previous, [service]: result }));
              } catch (caught) {
                setError(setupErrorMessage(caught));
              }
            }}
          />
          <button
            className="primary"
            type="button"
            disabled={busy || farthestStep(progress) !== "finish"}
            onClick={() => setCurrent("finish")}
          >
            Continue
          </button>
        </>
      )}
      {pane === "finish" && progress !== null && (
        <SetupFinishPane
          busy={busy}
          progress={progress}
          publicUrl={publicUrl}
          webhookSecret={webhookSecret}
          registrations={registrations}
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

function PublicUrlPane({
  busy,
  stored,
  onSubmit,
}: {
  busy: boolean;
  stored: boolean;
  onSubmit: (v: string) => Promise<boolean>;
}) {
  return (
    <OneFieldPane
      id="setup-public-url"
      title="This deployment's address"
      label="Autoposter's own URL"
      type="text"
      hint="https://autoposter.example.com — the address Radarr and Sonarr will send their webhooks to. Autoposter never fetches it."
      action="Continue"
      busy={busy}
      autoComplete="off"
      stored={stored}
      onSubmit={onSubmit}
    />
  );
}

function Shell({
  error,
  steps,
  current,
  onBack,
  children,
}: {
  error: string | null;
  steps: StepId[];
  current: StepId;
  /** Absent on the first step, which is what makes "no Back on step 1" a
   * property of the tree rather than a disabled button somebody can enable. */
  onBack?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="setup-screen">
      <div className="setup-box">
        <h1 className="setup-title">Set up Autoposter</h1>
        <Steps steps={steps} current={current} />
        {children}
        {onBack !== undefined && (
          <button className="setup-back" type="button" onClick={onBack}>
            Back
          </button>
        )}
        {error !== null && (
          <p className="page-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

function Steps({ steps, current }: { steps: StepId[]; current: StepId }) {
  const position = steps.indexOf(current);
  return (
    <ol className="setup-steps">
      {steps.map((id, index) => {
        const state = index < position ? "done" : index === position ? "current" : "todo";
        return (
          <li
            className={`setup-step ${state}`}
            key={id}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span aria-hidden="true" className="setup-step-number">
              {state === "done" ? "✓" : index + 1}
            </span>
            {STEP_LABELS[id]}
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
  stored,
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
  /** Whether the SERVER already holds a value for this step -- presence, which
   * is all `/progress` reports and all this needs. Absent for the password
   * pane, whose own label says which of set/prove it is asking for. It drives
   * both halves of facts C7: the pill that says the step is answered, and an
   * empty submit staying live, which the endpoints read as "keep what you
   * have". The value itself is never sent back to the page, so a step
   * navigated back into always shows an empty field. */
  stored?: boolean;
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
          {stored !== undefined && (
            <span className={`setup-pill${stored ? " ok" : ""}`} data-testid={`stored-${id}`}>
              {stored ? "Stored" : "Not set"}
            </span>
          )}
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
      {/* Empty stays submittable once the server holds a value, which is what
          "empty means keep" needs to be reachable: the step is answered, and
          re-typing what the wizard already staged is not a requirement it can
          impose on an operator who cannot see it. */}
      <button className="primary" type="submit" disabled={busy || (value === "" && stored !== true)}>
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
  stored,
  onSubmit,
}: {
  busy: boolean;
  stored: boolean;
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
      stored={stored}
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

/** The generated secret's header row: its human name, whether the server calls
 * it required, whether the deployment already holds it -- and, under those, the
 * environment name operators know it by.
 *
 * A `<span>` and never a `<label>`: this is the one credential with no input to
 * name, and it must not look as though it has one. Every other credential's
 * header is its accordion's own (SetupAccordion.tsx). */
function FieldHead({
  name,
  required,
  held,
}: {
  name: string;
  required: boolean;
  held: string | null;
}) {
  return (
    <>
      <div className="setup-field-head">
        <span className="setup-field-label">{providerLabel(name)}</span>
        {required && <span className="setup-badge">Required</span>}
        <span className={`setup-pill${held === null ? "" : " ok"}`} data-testid={`held-${name}`}>
          {held === null ? "Not set" : "Stored"}
        </span>
      </div>
      <code className="mono setup-field-env">{name}</code>
    </>
  );
}

/** The systems the registration route accepts -- `setup_arr.NAMES` rendered.
 * A system not here gets no button, which is the honest rendering for one this
 * wizard has no webhook to register with. */
const SYSTEMS_WITH_A_WEBHOOK = new Set(["radarr", "sonarr"]);

/** What goes inside one accordion's body, beneath its two fields, or nothing.
 *
 * A function rather than a ternary chain inline, because there are now two
 * kinds of extra body and a third would have made the JSX unreadable. Every
 * control it returns is `type="button"`: this renders inside the accordion's
 * credential `<form>`, and an unmarked button would submit that form instead of
 * doing its own job.
 */
function accordionBody(
  system: string,
  progress: SetupProgress,
  onSelectPlex: (plexUrl: string, excludedLibraries: string[]) => Promise<boolean>,
  onRegister: (service: string) => Promise<void>,
) {
  if (system === "plex") {
    return (fields: { address: string; credential: string; setAddress: (v: string) => void }) => (
      <SetupPlexPane
        address={fields.address}
        configSource={progress.config_source}
        credentialValue={fields.credential}
        onAddress={fields.setAddress}
        onSelect={onSelectPlex}
      />
    );
  }
  if (SYSTEMS_WITH_A_WEBHOOK.has(system)) {
    return () => (
      <div className="setup-field">
        <p className="setup-hint">
          Save the API key and run Check connection first: the registration uses the address that
          check proved, and the secret this wizard generated.
        </p>
        <button type="button" onClick={() => onRegister(system)}>
          Register the webhook for me
        </button>
      </div>
    );
  }
  return undefined;
}

/** The systems step: one collapsible per credential.
 *
 * The flat form this replaces rendered eleven inputs and two headings in one
 * column, which was already the longest pane in the wizard before Task 3 adds
 * a Plex sign-in flow and a library tick-list to it. Facts C8: required open,
 * optional collapsed, and the header carries both pills -- what the deployment
 * holds, and what the last check answered.
 *
 * The server owns the list and its order; the accordions keep the order they
 * were served in, and the Required/Optional headings go with the flat form --
 * the badge on each header says the same thing without splitting the list.
 */
function SystemsPane({
  busy,
  progress,
  onSave,
  onSelectPlex,
  onRegister,
}: {
  busy: boolean;
  progress: SetupProgress;
  onSave: (values: Record<string, string>) => Promise<boolean>;
  /** "Register the webhook for me", for the two systems that HAVE a webhook to
   * register. It reads nothing from this form: everything the route needs was
   * staged by an earlier step, which is why the refusals it can answer with are
   * step names. */
  onRegister: (service: string) => Promise<void>;
  /** The configuration step, which v2 stages from inside the Plex accordion
   * rather than from a bare "Plex server URL" box beside it: the address is
   * the one the operator just PICKED from their own account, or the one they
   * typed into the accordion when no plex.tv sign-in is possible, and the
   * tick-list that comes with it is the same submit's `excluded_libraries`.
   * ONE submit, two ways to arrive at it -- and it is the only writer of the
   * configuration document, which is what the wizard cannot finish without. */
  onSelectPlex: (plexUrl: string, excludedLibraries: string[]) => Promise<boolean>;
}) {
  const isRequired = (name: string) =>
    REQUIRED_PROVIDER_NAMES.includes(name) || progress.required.includes(name);

  return (
    <section className="setup-pane" data-testid="systems-step">
      <div className="setup-pane-head">
        <h2 className="setup-pane-title">Systems</h2>
      </div>
      <p className="setup-lead">
        A stored credential is shown as stored and never displayed; leave one blank to keep what is
        already there. Required systems are open; the rest are folded away until you need them.
      </p>
      {Object.entries(progress.providers).map(([name, held]) =>
        name === GENERATED_SECRET_NAME ? (
          <div className="setup-field" key={name}>
            <FieldHead held={held} name={name} required={isRequired(name)} />
            <p className="setup-hint">
              Generated for you when you save any system below, and shown once on the last step.
              There is nothing to paste here.
            </p>
          </div>
        ) : (
          <SetupAccordion
            key={name}
            credential={name}
            held={held}
            label={providerLabel(name)}
            needsAddress={SYSTEMS_WITH_AN_ADDRESS.has(SYSTEM_FOR_CREDENTIAL[name] ?? "")}
            required={isRequired(name)}
            system={SYSTEM_FOR_CREDENTIAL[name] ?? name}
            onSave={(credential, value) => onSave({ [credential]: value })}
          >
            {accordionBody(
              SYSTEM_FOR_CREDENTIAL[name] ?? name,
              progress,
              onSelectPlex,
              onRegister,
            )}
          </SetupAccordion>
        ),
      )}
      {busy && <p className="setup-hint">Working…</p>}
    </section>
  );
}
