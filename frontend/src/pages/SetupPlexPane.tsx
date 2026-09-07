import { useEffect, useRef, useState } from "react";

import {
  fetchPlexLibraries,
  fetchPlexServers,
  mintPlexPin,
  pollPlexPin,
  type PlexLibrary,
  type PlexServer,
} from "../api/setup";
import { setupErrorMessage } from "./Setup";

/** Sign in with Plex, pick the server, tick the libraries -- or type the
 * address and read them straight from it.
 *
 * TWO arrivals at ONE submit. The pick-list is the good path and the typed
 * address is the one that keeps the wizard finishable: `POST /api/setup/config`
 * is the only writer of the configuration document, the wizard cannot be
 * finished without one, and a completed plex.tv sign-in is not something every
 * deployment can produce -- plex.tv may be unreachable from the pod, the server
 * may be linked to no plex.tv account, the operator may already hold the token,
 * and the picked connection may be one the pod cannot route to (which is why
 * the accordion has a typed address field at all). Both arrivals read the same
 * libraries route and post the same submit; a second submit would be a second
 * place to compute the exclusion complement wrongly.
 *
 * The poll is CLIENT-side, at two seconds -- the `Restarting` component's
 * existing cadence -- with a hard stop at the PIN's expiry. The server holds
 * no background poll: a task outliving its request in an application that has
 * no lifespan is a task nothing shuts down.
 *
 * Nothing here ever holds a token. The poll answers a boolean; the account
 * token is staged server-side by the route that received it.
 *
 * Every control is `type="button"`: this pane renders inside the accordion's
 * credential `<form>`, so an unmarked button would submit that form instead of
 * doing its own job.
 */
const POLL_INTERVAL_MS = 2000;

/** A deployment whose configuration document the DEPLOYMENT provides.
 *
 * `POST /api/setup/config` is refused there (facts Amendment 6), so the submit
 * that would post to it is replaced by this rather than rendered to fail. The
 * public URL's stage-but-do-not-persist consolation has no equivalent here: the
 * Plex address and the excluded libraries live IN the document, and the
 * document is not this wizard's. The sign-in stays offered, because it still
 * stages the account token. */
const CONFIGURED_DOCUMENT =
  "This deployment provides its own configuration document, so the Plex address and the libraries are read from that and cannot be set here. Signing in still stores the Plex token.";

/** The library read failed and the address is still worth recording.
 *
 * Submitting with an EMPTY exclusion list is the honest answer -- every library
 * managed -- and it is a different answer from absent, which would leave the
 * example document's own two exclusions on this deployment. */
const LIBRARIES_UNREAD =
  "The libraries at that address could not be read. You can still use the address; every library will be managed.";

export function SetupPlexPane({
  address = "",
  configSource = null,
  credentialValue = "",
  onAddress,
  onSelect,
}: {
  /** The accordion's own address field. The MANUAL arrival's first input: the
   * sign-in is not the only way a real deployment reaches the configuration
   * document, because plex.tv can be unreachable from the pod, the server can
   * be linked to no plex.tv account, and the picked connection can be one the
   * pod cannot route to. */
  address?: string;
  /** `progress.config_source`. `"configured"` is the one value that changes
   * what this pane offers -- see `CONFIGURED_DOCUMENT`. */
  configSource?: "configured" | "state" | "staged" | null;
  /** The accordion's own credential field, sent as the libraries read's
   * `credential_value`: the manual arrival's token has been typed and not yet
   * saved. Empty means the deployment's own, the rule every field here has. */
  credentialValue?: string;
  /** The picked connection, handed straight to the accordion's own `address`
   * state -- the only thing its Check button reads. Called as soon as the
   * operator picks, not at submit: a picked server that has not landed there
   * is checked against an empty string. */
  onAddress?: (plexUrl: string) => void;
  /** `(plexUrl, excludedLibraries)` -- the tick-list's COMPLEMENT, because
   * the schema's field is `plex.excluded_libraries`. */
  onSelect: (plexUrl: string, excludedLibraries: string[]) => Promise<boolean>;
}) {
  const [code, setCode] = useState<string | null>(null);
  const [authUrl, setAuthUrl] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  const [servers, setServers] = useState<PlexServer[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [libraries, setLibraries] = useState<PlexLibrary[] | null>(null);
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [unread, setUnread] = useState(false);
  const deadline = useRef<number>(0);

  // The one thing `configured` changes: there is nothing to submit to.
  const canSubmit = configSource !== "configured";

  async function signIn() {
    setError(null);
    setExpired(false);
    try {
      const pin = await mintPlexPin();
      deadline.current = Date.now() + pin.expires_in * 1000;
      setCode(pin.code);
      setAuthUrl(pin.auth_url);
    } catch (caught) {
      setError(setupErrorMessage(caught));
    }
  }

  useEffect(() => {
    if (code === null || servers !== null || expired) return undefined;
    const timer = window.setInterval(() => {
      if (Date.now() >= deadline.current) {
        setExpired(true);
        return;
      }
      pollPlexPin()
        .then(async (state) => {
          if (!state.authorised) return;
          setServers((await fetchPlexServers()).servers);
        })
        .catch((caught) => setError(setupErrorMessage(caught)));
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [code, servers, expired]);

  /** The libraries at one address, and the tick-list they become.
   *
   * Both arrivals end here and then at the same submit; they differ only in
   * where the address came from and whether a typed token goes with it. Every
   * library is ticked to begin with, because the operator is choosing what to
   * LEAVE OUT and the example config's own two exclusions belong to somebody
   * else's deployment.
   */
  async function readLibraries(plexUrl: string, credential: string | null) {
    setError(null);
    setUnread(false);
    setChosen(plexUrl);
    onAddress?.(plexUrl);
    try {
      const listed = (await fetchPlexLibraries(plexUrl, credential)).libraries;
      setLibraries(listed);
      setTicked(Object.fromEntries(listed.map((library) => [library.title, true])));
    } catch (caught) {
      // The read failed; the ADDRESS is still worth recording, and the
      // alternative is a wizard that cannot be finished. The tick-list becomes
      // empty and the submit stays, so what is sent is no exclusions at all.
      setError(setupErrorMessage(caught));
      setLibraries([]);
      setTicked({});
      setUnread(true);
    }
  }

  /** A picked connection reads with the token the sign-in staged, so no
   * credential goes with it. */
  function chooseServer(uri: string) {
    return readLibraries(uri, null);
  }

  function useTypedAddress() {
    return readLibraries(address.trim(), credentialValue === "" ? null : credentialValue);
  }

  return (
    <div className="setup-plex">
      {!canSubmit && (
        <p className="setup-hint" data-testid="plex-configured">
          {CONFIGURED_DOCUMENT}
        </p>
      )}
      {code === null ? (
        <button type="button" onClick={signIn}>
          Sign in with Plex
        </button>
      ) : (
        <div className="setup-field">
          <p className="setup-hint">
            Open the link and approve the sign-in; this panel continues by itself. The link already
            carries the code below — there is nothing to type.
          </p>
          <code className="mono setup-secret-value" data-testid="plex-pin-code">
            {code}
          </code>
          {authUrl !== null && (
            <a href={authUrl} rel="noreferrer" target="_blank">
              Open the Plex sign-in
            </a>
          )}
          {expired && (
            <p className="setup-hint" data-testid="plex-pin-expired">
              That code has expired. Start the sign-in again.
            </p>
          )}
        </div>
      )}

      {canSubmit && servers === null && address.trim() !== "" && (
        <div className="setup-field">
          <p className="setup-hint">
            Or skip the sign-in: with the address and the token above, the libraries can be read
            straight from that server.
          </p>
          <button type="button" onClick={useTypedAddress}>
            Use this address
          </button>
        </div>
      )}

      {servers !== null && (
        <fieldset className="setup-field">
          <legend className="setup-field-label">This account&apos;s servers</legend>
          {servers.map((server) => (
            <div key={server.client_identifier}>
              <span className="setup-field-label">{server.name}</span>
              {server.connections.map((connection) => (
                <label key={connection.uri}>
                  <input
                    checked={chosen === connection.uri}
                    name="plex-server"
                    type="radio"
                    value={connection.uri}
                    onChange={() => chooseServer(connection.uri)}
                  />
                  {connection.uri}
                  {connection.local ? " (local)" : ""}
                </label>
              ))}
            </div>
          ))}
        </fieldset>
      )}

      {libraries !== null && chosen !== null && (
        <fieldset className="setup-field">
          <legend className="setup-field-label">Libraries to manage</legend>
          {unread && (
            <p className="setup-hint" data-testid="plex-libraries-unread">
              {LIBRARIES_UNREAD}
            </p>
          )}
          {libraries.map((library) => (
            <label key={library.key} htmlFor={`plex-library-${library.key}`}>
              <input
                checked={ticked[library.title] ?? true}
                id={`plex-library-${library.key}`}
                type="checkbox"
                onChange={(event) =>
                  setTicked((previous) => ({
                    ...previous,
                    [library.title]: event.target.checked,
                  }))
                }
              />
              {library.title}
            </label>
          ))}
          {canSubmit && (
            <button
              type="button"
              onClick={() =>
                onSelect(
                  chosen,
                  libraries
                    .map((library) => library.title)
                    .filter((title) => !(ticked[title] ?? true)),
                )
              }
            >
              Use this server
            </button>
          )}
        </fieldset>
      )}

      {error !== null && (
        <p className="page-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
