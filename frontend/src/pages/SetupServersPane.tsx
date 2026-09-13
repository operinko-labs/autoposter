import type { SetupProgress } from "../api/setup";
import { SYSTEMS_WITH_AN_ADDRESS } from "./Setup";
import { SetupAccordion } from "./SetupAccordion";
import { SetupJellyfinPane } from "./SetupJellyfinPane";
import { SetupPlexPane } from "./SetupPlexPane";

/** The media servers this wizard can set up, and the credential each one's
 * own secret is known by -- `api/setup._SERVER_CREDENTIAL` rendered, keyed the
 * way the configuration document and `/progress.servers` key it. A third
 * server would be added in both places, because a card the server has no line
 * for is a card whose pills nothing can answer.
 *
 * The label is the SERVER's name and not the credential's: this step's cards
 * are servers, and the address field beneath each one reads "Plex address"
 * rather than "Plex token address" because of it. */
const SERVER_CARDS = [
  { name: "plex", label: "Plex", credential: "AUTOPOSTER_PLEX_TOKEN" },
  { name: "jellyfin", label: "Jellyfin", credential: "AUTOPOSTER_JELLYFIN_APIKEY" },
];

/** What `held` means for a card, given a line that reports presence as a
 * BOOLEAN rather than as the redaction marker the provider map carries.
 *
 * The accordion asks its `held` one question -- `=== null` -- and the two
 * server credentials are deliberately absent from `providers`, so this is the
 * translation between the one surface that reports them and the one component
 * that renders them. The marker is never displayed: the pill beside it reads
 * "Stored". */
const HELD = "***REDACTED***";

/** The media-server step: one card per server, either of which finishes it.
 *
 * A deployment runs on Plex, on Jellyfin, or on both (spec 8), so this step is
 * two independent cards and not a choice between them -- each holds that
 * server's address, its credential, its check, and its own library tick-list,
 * and each submits ONLY its own pair of fields. The step is answered by the
 * server's own rule (`farthestStep`), never by this pane: one complete server,
 * and no configured server left without its credential.
 *
 * One card opens: the one the document names and holds no credential for --
 * the one shape on this step an operator MUST act on -- or, on a deployment
 * with nothing configured at all, Plex's, which is the pane the Plex-only
 * operator used to land on with the sign-in expanded. Never both: the Plex
 * card carries a whole plex.tv sign-in flow inside it, and two open cards is
 * the longest pane in the wizard for an operator who runs one server.
 */
export function SetupServersPane({
  busy,
  progress,
  onSave,
  onSelect,
}: {
  busy: boolean;
  progress: SetupProgress;
  onSave: (values: Record<string, string>) => Promise<boolean>;
  /** The configuration step's one submit, in two pairs -- one per card. The
   * card that was used fills its own pair and leaves the other's `null`, which
   * is what lets a Jellyfin-only deployment write a document with no `plex:`
   * block in it and what keeps a card saved on its own from clearing the
   * other server's. */
  onSelect: (
    plexUrl: string | null,
    excludedLibraries: string[] | null,
    jellyfinUrl: string | null,
    jellyfinExcludedLibraries: string[] | null,
  ) => Promise<boolean>;
}) {
  // A deployment with NO server configured at all is a first run, and the card
  // it opens is Plex's: that is where the Plex-only operator used to land --
  // the token was a required provider key, so its accordion came up expanded
  // with the sign-in inside it -- and two closed cards under a disabled
  // Continue is the one visible thing this step changed about that walk
  // (review M3). A deployment the document already names keeps the split
  // below: the card that opens is the one missing its credential.
  const firstRun = SERVER_CARDS.every((card) => !progress.servers[card.name]?.configured);

  return (
    <section className="setup-pane" data-testid="servers-step">
      <div className="setup-pane-head">
        <h2 className="setup-pane-title">Media servers</h2>
      </div>
      <p className="setup-lead">
        Autoposter manages Plex, Jellyfin, or both. Open the card for the server this deployment
        runs, give it an address and its credential, and pick the libraries it should manage — one
        finished card is enough to carry on.
      </p>
      {SERVER_CARDS.map((card) => {
        const state = progress.servers[card.name] ?? {
          configured: false,
          credential: false,
          checked: false,
        };
        return (
          <SetupAccordion
            key={card.name}
            credential={card.credential}
            defaultOpen={firstRun && card.name === "plex"}
            held={state.credential ? HELD : null}
            label={card.label}
            needsAddress={SYSTEMS_WITH_AN_ADDRESS.has(card.name)}
            // The gate's second clause, rendered: a server the document names
            // is one the next boot demands a credential for, so that card's
            // credential is required -- and required-and-missing is what opens
            // a card (facts C8).
            required={state.configured}
            system={card.name}
            onSave={(credential, value) => onSave({ [credential]: value })}
          >
            {serverBody(card.name, progress, onSelect)}
          </SetupAccordion>
        );
      })}
      {busy && <p className="setup-hint">Working…</p>}
    </section>
  );
}

/** One card's body, over its own two fields.
 *
 * A render prop, because both panes read the address and the credential the
 * ACCORDION holds: a second pair inside a pane would be two places for one
 * answer and the Check button would read the wrong one. Only Plex writes back
 * into the address, which is the sign-in's picked connection arriving.
 */
function serverBody(
  server: string,
  progress: SetupProgress,
  onSelect: (
    plexUrl: string | null,
    excludedLibraries: string[] | null,
    jellyfinUrl: string | null,
    jellyfinExcludedLibraries: string[] | null,
  ) => Promise<boolean>,
) {
  if (server === "jellyfin") {
    return (fields: { address: string; credential: string }) => (
      <SetupJellyfinPane
        address={fields.address}
        configSource={progress.config_source}
        credentialValue={fields.credential}
        onSelect={onSelect}
      />
    );
  }
  return (fields: { address: string; credential: string; setAddress: (v: string) => void }) => (
    <SetupPlexPane
      address={fields.address}
      configSource={progress.config_source}
      credentialValue={fields.credential}
      onAddress={fields.setAddress}
      onSelect={(plexUrl, excluded) => onSelect(plexUrl, excluded, null, null)}
    />
  );
}
