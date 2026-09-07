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

/** Sign in with Plex, pick the server, tick the libraries.
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

export function SetupPlexPane({
  onAddress,
  onSelect,
}: {
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
  const deadline = useRef<number>(0);

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

  async function chooseServer(uri: string) {
    setChosen(uri);
    onAddress?.(uri);
    try {
      const listed = (await fetchPlexLibraries(uri)).libraries;
      setLibraries(listed);
      // Every library ticked to begin with: the operator is choosing what to
      // LEAVE OUT, and the example config's own two exclusions belong to
      // somebody else's deployment.
      setTicked(Object.fromEntries(listed.map((library) => [library.title, true])));
    } catch (caught) {
      setError(setupErrorMessage(caught));
    }
  }

  return (
    <div className="setup-plex">
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
