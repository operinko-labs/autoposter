import type { ArrRegistration, SetupProgress } from "../api/setup";
import { providerLabel } from "./Setup";
import { WebhookSecret } from "./WebhookSecret";

/** The two services this wizard registers a webhook with, in the order the
 * finish page lists them, and the provider name that answers whether each is
 * even staged. `setup_arr.NAMES` is the server's own allowlist and this is its
 * rendering; a third *arr would be added in both places, because the page
 * must not offer a registration the route refuses. */
const ARR_SERVICES = [
  { service: "sonarr", label: "Sonarr", apiKey: "AUTOPOSTER_SONARR_APIKEY" },
  { service: "radarr", label: "Radarr", apiKey: "AUTOPOSTER_RADARR_APIKEY" },
];

/** The media servers, in the order the finish page lists them -- the same
 * cards the media-server step offered, so an operator reads the two pages in
 * one order. `/progress.servers` is keyed by these names. */
const MEDIA_SERVERS = [
  { name: "plex", label: "Plex" },
  { name: "jellyfin", label: "Jellyfin" },
];

/** A server this deployment does not run: no address in the document, so
 * nothing was written for it and nothing is missing. The wizard is finishable
 * with either server (spec 8), which is what makes this a shape and not an
 * omission -- and saying so here is what tells a Jellyfin-only operator that
 * the empty Plex card was read as an answer. */
const SERVER_LEFT = "Left for later — no address was given for it, so the configuration names it nowhere.";

/** Its address is in the document and its credential is not stored. The next
 * boot refuses this (`missing_server_setup`), and this is the last page that
 * can say so. */
const SERVER_WITHOUT_CREDENTIAL =
  "An address is configured, but its credential is not stored — this deployment will not start until it is.";

/** A check DID pass, during setup. Worded as an event and never as a property
 * of the address: the wizard records checks for the session it runs in and
 * forgets them, so "this address was checked" would be a claim nothing on the
 * other side of the restart can stand behind. */
const SERVER_CHECKED = "Configured, and a check passed during setup.";

/** No check passed, which is not a failure: the check never gated this step,
 * and a wizard picked up in a second tab starts with none recorded. */
const SERVER_UNCHECKED =
  "Configured. No check passed during setup — the wizard only records checks for the session it ran in.";

/** A registration nobody asked for, on a service this wizard is otherwise
 * READY to register: an API key is staged and an address was checked. Not a
 * failure and not a success: the operator simply did not press the button,
 * and the honest next instruction is the one they follow today. */
const NOT_ATTEMPTED =
  "Not attempted — paste the secret into that service's Webhook connection by hand.";

/** A service this deployment does not appear to run at all -- no API key
 * staged, or no address ever checked. Distinct from `NOT_ATTEMPTED` (the
 * Missing, task 4 review): "paste it in by hand" is the wrong instruction for
 * a service the operator never set up, and conflating the two tells them to
 * go configure something they deliberately left out. */
const NOT_CONFIGURED = "Not configured — no API key staged, or its address was never checked.";

/** What happened, once, before the process is replaced.
 *
 * Four blocks, and three of them are what v1 had nowhere to put: the secret the
 * wizard minted (shown HERE rather than mid-wizard, so an operator whose
 * registrations succeeded can see they need not paste it anywhere), the
 * registration results, which media server this deployment was set up with,
 * and everything that was skipped -- credentials left empty, and the
 * DEPLOYMENT-SHAPE skips that are not the operator's doing.
 *
 * The last of those is the one the operator cannot find out any other way
 * (facts C1). On a deployment whose configuration document already resolves,
 * `finish` writes no document at all -- it writes one only
 * `if state.config_document is not None`, and `stage_config_document` refuses
 * to stage one while a document resolves -- so three things are true at once
 * and all three are said by name: `public_url` was USED for the registrations
 * above and not persisted; the Plex address and the ticked libraries were
 * recorded nowhere, because `base_urls` dies with the setup state at `execv`;
 * and the Plex account token DID persist, into the secrets file. Left unsaid,
 * the operator leaves believing the tick-list took effect.
 */
export function SetupFinishPane({
  busy,
  progress,
  publicUrl,
  webhookSecret,
  registrations,
  onSubmit,
}: {
  busy: boolean;
  progress: SetupProgress;
  /** Shown because it is not a credential: it is `public_url` plus a fixed
   * path, and the operator has to be able to check it against what their
   * reverse proxy actually serves. The header VALUE is never shown beside it --
   * the secret has exactly one place on this page. `null` while the page does
   * not hold it, since the server never serves it back. */
  publicUrl: string | null;
  webhookSecret: string | null;
  /** Service key -> what the route answered. A service absent from this map was
   * never attempted, which is a third state and is rendered as one. */
  registrations: Record<string, ArrRegistration>;
  onSubmit: () => void;
}) {
  const skipped = Object.entries(progress.providers).filter(([, held]) => held === null);
  // `"staged"` is the ONE source that means this wizard holds a document for
  // `finish` to write; `"configured"` and `"state"` both mean one already
  // resolves and nothing of `public_url` will land. The plain `config` boolean
  // is true for all three and cannot be asked.
  const documentIsOurs = progress.config_source === "staged";

  return (
    <section className="setup-pane">
      <div className="setup-pane-head">
        <h2 className="setup-pane-title">Ready</h2>
      </div>

      {webhookSecret !== null && <WebhookSecret value={webhookSecret} />}

      <section className="setup-group setup-report">
        <h3 className="setup-group-title">Webhook registrations</h3>
        {ARR_SERVICES.map(({ service, label, apiKey }) => {
          const result = registrations[service];
          // No key staged, or no successful check for this service -- a
          // deployment shape, not something the operator forgot to press.
          // `== null` on purpose: a provider name the fixture never set is the
          // same "nothing staged" as one the server reported `null` for.
          const configured =
            progress.providers[apiKey] != null && progress.checked_systems.includes(service);
          return (
            <p className="setup-hint" data-testid={`registration-${service}`} key={service}>
              <strong>{label}:</strong>{" "}
              {result === undefined
                ? configured
                  ? NOT_ATTEMPTED
                  : NOT_CONFIGURED
                : result.ok && publicUrl !== null
                  ? `${result.detail} ${publicUrl}/webhook/${service}`
                  : result.detail}
            </p>
          );
        })}
      </section>

      <section className="setup-group setup-report" data-testid="media-servers">
        <h3 className="setup-group-title">Media servers</h3>
        {MEDIA_SERVERS.map(({ name, label }) => {
          const server = progress.servers[name];
          return (
            <p className="setup-hint" data-testid={`server-${name}`} key={name}>
              <strong>{label}:</strong>{" "}
              {server === undefined || !server.configured
                ? SERVER_LEFT
                : !server.credential
                  ? SERVER_WITHOUT_CREDENTIAL
                  : server.checked
                    ? SERVER_CHECKED
                    : SERVER_UNCHECKED}
            </p>
          );
        })}
      </section>

      <section className="setup-group setup-report" data-testid="skipped-list">
        <h3 className="setup-group-title">Left for later</h3>
        {skipped.map(([name]) => (
          <p className="setup-hint" key={name}>
            {providerLabel(name)} — <code className="mono setup-field-env">{name}</code>
          </p>
        ))}
        {progress.database_source === "resolved" && (
          <p className="setup-hint">
            The database step was skipped: this deployment&apos;s environment already resolves a
            database URL.
          </p>
        )}
        {!documentIsOurs && (
          <>
            <p className="setup-hint">
              <code className="mono setup-field-env">public_url</code> was used for the
              registrations above but not written: this deployment&apos;s configuration document is
              already supplied, so the wizard cannot edit it. Add{" "}
              <code className="mono setup-field-env">public_url</code> to that document if you want
              the Settings page to re-register for you later.
            </p>
            <p className="setup-hint">
              For the same reason the Plex address and the libraries you ticked were not recorded
              anywhere — they live in that document. The Plex token was stored, in the secrets
              file, and so is every other credential above.
            </p>
          </>
        )}
      </section>

      <p className="setup-lead">
        Everything Autoposter needs is set. Starting it restarts this service once.
      </p>
      <button className="primary" type="button" disabled={busy} onClick={onSubmit}>
        Start autoposter
      </button>
    </section>
  );
}
