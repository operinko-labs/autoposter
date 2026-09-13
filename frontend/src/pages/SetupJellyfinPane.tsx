import { useState } from "react";

import { fetchJellyfinLibraries, type JellyfinLibrary } from "../api/setup";
import { setupErrorMessage } from "./Setup";

/** Read the Jellyfin server's libraries, tick the ones to manage, submit.
 *
 * `SetupPlexPane`'s tick-list half, with ONE arrival where that pane has two:
 * Jellyfin has no account sign-in to pick a server from, so the address and
 * the API key are always the operator's own -- the two fields the accordion
 * above this pane holds, handed down as props so that the Check button beside
 * them and the read below them can never disagree about which address is
 * meant.
 *
 * Nothing here holds a credential of its own. The key is the accordion's, it
 * is sent for one read and staged nowhere, and an empty one means "use the one
 * the wizard already has" -- the rule every credential field in this wizard
 * has.
 *
 * Every control is `type="button"`: this renders inside the accordion's
 * credential `<form>`, so an unmarked button would submit that form instead of
 * doing its own job.
 */

/** A deployment whose configuration document the DEPLOYMENT provides.
 *
 * `POST /api/setup/config` is refused there (facts Amendment 6), so the
 * controls that post to it are replaced by this rather than rendered to fail.
 * Its own sentence and not the Plex pane's: an operator who mistyped nothing
 * about Plex must not be told about Plex. The API key field above stays --
 * this is exactly the deployment whose document may name Jellyfin with no key
 * stored, which is the media-server step's second clause. */
const CONFIGURED_DOCUMENT =
  "This deployment provides its own configuration document, so the Jellyfin address and the libraries are read from that and cannot be set here. Saving the API key still stores it.";

/** A document THIS WIZARD wrote at the state path on an earlier run.
 *
 * `stage_config_document` refuses on `config_document_path() is not None`,
 * which is `"configured"` and `"state"` alike -- so the submit goes for the
 * same reason. A second sentence rather than the one above widened, because
 * that one's first clause is false here: the deployment does not provide this
 * document, an earlier finish of this wizard wrote it. */
const STATE_DOCUMENT =
  "A previous run of this wizard already wrote this deployment's configuration document, so the Jellyfin address and the libraries are read from that and cannot be set again here. Saving the API key still stores it.";

/** The library read failed and the address is still worth recording.
 *
 * Submitting with an EMPTY exclusion list is the honest answer -- every
 * library managed -- and it is a different answer from absent, which would
 * leave the example document's own exclusions on this deployment. */
const LIBRARIES_UNREAD =
  "The libraries at that address could not be read. You can still use the address; every library will be managed.";

export function SetupJellyfinPane({
  address = "",
  configSource = null,
  credentialValue = "",
  onSelect,
}: {
  /** The accordion's own address field, and this pane's only input. */
  address?: string;
  /** `progress.config_source`. A document that already resolves is one this
   * step cannot write to -- see `CONFIGURED_DOCUMENT`. */
  configSource?: "configured" | "state" | "staged" | null;
  /** The accordion's own credential field, sent as the read's
   * `credential_value`: the key has been typed and not necessarily saved.
   * Empty means the one the wizard holds. */
  credentialValue?: string;
  /** The media-server step's one submit, whose four fields are two pairs --
   * one per card. This card fills its own pair and leaves the Plex pair
   * `null`, because a body that named an address for a server this operator
   * never configured would write a block the next boot then demands a
   * credential for. */
  onSelect: (
    plexUrl: string | null,
    excludedLibraries: string[] | null,
    jellyfinUrl: string | null,
    jellyfinExcludedLibraries: string[] | null,
  ) => Promise<boolean>;
}) {
  const [libraries, setLibraries] = useState<JellyfinLibrary[] | null>(null);
  const [ticked, setTicked] = useState<Record<string, boolean>>({});
  const [chosen, setChosen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unread, setUnread] = useState(false);

  // The one thing a RESOLVING document changes: there is nothing to submit to.
  // Two values and not one -- the server rule is
  // `config_document_path() is not None`, which `"state"` satisfies as surely
  // as `"configured"` does.
  const canSubmit = configSource !== "configured" && configSource !== "state";
  const typed = address.trim();

  /** The libraries at the typed address, and the tick-list they become.
   *
   * Every library is ticked to begin with, because the operator is choosing
   * what to LEAVE OUT and the example document's own exclusions belong to
   * somebody else's deployment. The exclusions are spelled by NAME: the
   * schema's `jellyfin.excluded_libraries` is a list of library names, and
   * `id` is the handle this route answers with and nothing the document
   * carries.
   */
  async function readLibraries() {
    setError(null);
    setUnread(false);
    setChosen(typed);
    try {
      const listed = (
        await fetchJellyfinLibraries(typed, credentialValue === "" ? null : credentialValue)
      ).libraries;
      setLibraries(listed);
      setTicked(Object.fromEntries(listed.map((library) => [library.name, true])));
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

  return (
    <div className="setup-server">
      {configSource === "configured" && (
        <p className="setup-hint" data-testid="jellyfin-configured">
          {CONFIGURED_DOCUMENT}
        </p>
      )}
      {configSource === "state" && (
        <p className="setup-hint" data-testid="jellyfin-state-document">
          {STATE_DOCUMENT}
        </p>
      )}

      {canSubmit && typed !== "" && (
        <div className="setup-field">
          <p className="setup-hint">
            With the address and the API key above, the libraries can be read straight from that
            server.
          </p>
          <button type="button" onClick={readLibraries}>
            Read libraries
          </button>
        </div>
      )}

      {libraries !== null && chosen !== null && (
        <fieldset className="setup-field">
          <legend className="setup-field-label">Libraries to manage</legend>
          {unread && (
            <p className="setup-hint" data-testid="jellyfin-libraries-unread">
              {LIBRARIES_UNREAD}
            </p>
          )}
          {libraries.map((library) => (
            <label key={library.id} htmlFor={`jellyfin-library-${library.id}`}>
              <input
                checked={ticked[library.name] ?? true}
                id={`jellyfin-library-${library.id}`}
                type="checkbox"
                onChange={(event) =>
                  setTicked((previous) => ({ ...previous, [library.name]: event.target.checked }))
                }
              />
              {library.name}
            </label>
          ))}
          {canSubmit && (
            <button
              type="button"
              onClick={() =>
                onSelect(
                  null,
                  null,
                  chosen,
                  libraries
                    .map((library) => library.name)
                    .filter((name) => !(ticked[name] ?? true)),
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
