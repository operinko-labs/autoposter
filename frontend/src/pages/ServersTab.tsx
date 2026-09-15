/** The Servers tab: one accordion per server, an Add a server row, and the
 * library map.
 *
 * THIS TAB NEVER TOUCHES THE PAGE'S PENDING CHANGE. A server is a unit --
 * address, credential and exclusions belong together -- so each card saves on
 * its own, and so does the map. What the tab owes the page in return is the
 * re-read: `changed` pulls `/api/servers` again and then awaits the page's
 * own `onChanged`, so the listing a card was re-rendered from and the
 * configuration the switches are read out of move together after every write.
 *
 * THAT RE-READ IS WHY A WRITE IS REFUSED WHILE THE PAGE IS DIRTY. The page's
 * half of it re-seeds the editor from the server, which silently discards an
 * unsaved edit made on any other tab -- so `pendingEdits` disables every
 * control here whose success asks for it, in the same sentence the restart
 * banner and the two System-tab panels use. The catch-up buttons are the
 * exception and ask for the listing alone: they change no setting, so there is
 * nothing on the page for them to refresh and nothing of the operator's to
 * lose.
 *
 * THE SWITCHES COME FROM THE PAGE'S CONFIGURATION, NOT FROM A SECOND READ.
 * `badges.upload_to_plex` and its four siblings are ordinary settings that
 * happen to be about one server; the page already holds the served document,
 * so the tab reads the five leaves out of it rather than fetching a copy that
 * could disagree with the tab beside it. A missing or non-boolean leaf reads
 * as off, which is what the schema's default is and what the route stores
 * when a box is ticked for the first time.
 *
 * THE OPEN CARDS ARE THIS TAB'S OWN, and deliberately not the page's
 * remembered-section state: that one is "the section I was last reading",
 * remembered per browser, and it holds exactly one section open. The rule
 * here is "the card that needs attention", computed once from the first
 * listing and then left to the operator. Persisting it would mean a card that
 * opened itself because a credential was missing kept opening itself after
 * the credential was stored.
 */
import { useEffect, useMemo, useRef, useState } from "react";

import { readPath, refusalMessage } from "../api/overrides";
import { fetchServers, type ServerRow } from "../api/servers";
import type { ConfigResponse } from "../api/types";
import { LibraryMapPanel } from "./LibraryMapPanel";
import { LABELS, ServerCard, SWITCHES } from "./ServerCard";
import { SettingsAccordion } from "./SettingsAccordion";

/** Every switch a card on this tab can show, as a dotted path into the served
 * configuration.
 *
 * DERIVED from the card's own table rather than listed again: a switch added
 * there and forgotten here would read `false` whatever the document said, and
 * the box would show off over a setting that is on. One list for both cards,
 * because the card keys its SAVE off its own half of that table -- a path
 * meant for the other server is read and handed over but can never be sent. */
const SWITCH_PATHS = Object.values(SWITCHES).flatMap((entries) =>
  entries.map((entry) => entry.path),
);

/** What the five switches are set to now.
 *
 * Anything that is not a boolean reads as off -- an absent leaf (the schema's
 * default applies), and equally a leaf holding something else, which is a
 * document no save from this page could have produced and which must not
 * reach a tick box as a truthy value. */
function switchValues(config: ConfigResponse | null): Record<string, boolean> {
  const values: Record<string, boolean> = {};
  for (const path of SWITCH_PATHS) {
    const found = config === null ? undefined : readPath(config, path);
    values[path] = typeof found === "boolean" ? found : false;
  }
  return values;
}

/** Which cards open by themselves: the ones that need attention, which is
 * exactly one shape -- a configured server with no credential, because that
 * deployment will not boot.
 *
 * There is no fresh-deployment case. A served page always has at least one
 * configured server: the schema refuses a document with neither block, and an
 * address is required in both, so the listing this reads can never be all
 * unconfigured. A card is rendered only for a configured server or one picked
 * from Add a server, so a name returned for a server in neither state would
 * open nothing at all.
 *
 * Extracted as a function rather than inlined so the rule can be tested
 * without rendering. */
export function cardsToOpen(servers: ServerRow[]): string[] {
  return servers
    .filter(
      (server) => server.configured && server.credential_source === "unset",
    )
    .map((server) => server.name);
}

export function ServersTab({
  config,
  revision,
  pendingEdits = false,
  onChanged,
}: {
  /** The configuration the page was served, which is where the five switches
   * are read from. Null while the page is still loading it; the cards then
   * show every switch off, which is the same thing an empty document says. */
  config: ConfigResponse | null;
  /** The configuration revision the page was served. Both of a card's writes
   * and the map's are checked against it, so a page served none renders the
   * cards read-only rather than sending a write the routes will refuse. */
  revision: string | null;
  /** Whether the page is holding an edit nobody has stored. Handed down
   * unchanged: the page's re-read re-seeds the editor from the server, so
   * every control here whose success asks for that re-read is refused while
   * an edit is outstanding. */
  pendingEdits?: boolean;
  onChanged: () => Promise<void> | void;
}) {
  const [servers, setServers] = useState<ServerRow[] | null>(null);
  const [added, setAdded] = useState<string[]>([]);
  /** What a removal answered, said here rather than on the card that made it:
   * the re-read below takes that card off the screen. */
  const [removal, setRemoval] = useState<string | null>(null);
  const [open, setOpen] = useState<string[]>([]);
  const [mapOpen, setMapOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped by Try again, and by nothing else: a listing that failed stays
  // failed until it is asked for again, because a tab that retried on its own
  // would hammer a deployment whose database is down and would do it behind
  // an operator who is reading the sentence saying so.
  const [attempt, setAttempt] = useState(0);

  /** Whether the open set has been seeded from a listing yet.
   *
   * The rule is about the first listing of this visit, not about every one:
   * re-seeding on the re-read that follows a save would slam shut the card
   * whose credential was just stored, mid-edit, and re-open one another
   * admin's write had just left without a credential. A ref rather than
   * state because changing it is never a reason to render. */
  const seeded = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetchServers()
      .then((found) => {
        if (cancelled) return;
        setServers(found);
        setError(null);
        if (!seeded.current) {
          seeded.current = true;
          setOpen(cardsToOpen(found));
        }
      })
      .catch((caught: unknown) => {
        // The server's own sentence, whatever shape it arrived in.
        if (!cancelled) setError(refusalMessage(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  /** What a card or the map calls after a write.
   *
   * The listing first, then the page: the card is re-rendered from the
   * listing (its address, its credential source, its restart pill) and the
   * switches come from the page's configuration, so the two reads are the two
   * halves of one card and a write is only settled once both have landed. The
   * page is re-read even when the listing failed -- the write happened, and
   * the configuration is a separate read that has no reason to be skipped.
   *
   * NEITHER HALF IS ALLOWED TO THROW BACK INTO THE CARD. A card awaits this
   * inside the same block that catches its write's refusal, so a re-read that
   * failed after a write that landed would be rendered as that write's own
   * error -- and would take the card's "Stored ...'s credential" note with it,
   * which is an operator typing a stored secret in a second time. Both are
   * reported here, where they are what they are: the write happened, and the
   * screen is one read behind. */
  async function changed({ page = true }: { page?: boolean } = {}) {
    try {
      setServers(await fetchServers());
      setError(null);
    } catch (caught) {
      setError(refusalMessage(caught));
    }
    // `page: false` is for a press that changed no setting -- the catch-up
    // trio, which moves rows in the outcome tables and nothing in the stored
    // document. The page's re-read re-seeds its editor from the server, so
    // asking for it there would throw away an unsaved edit on another tab to
    // refresh something that has not moved.
    if (!page) return;
    try {
      await onChanged();
    } catch (caught) {
      setError(refusalMessage(caught));
    }
  }

  /** What a card's removal answered, in this tab's words.
   *
   * The card is unmounted by the re-read that follows -- the listing comes
   * back with that server unconfigured -- so this is where the sentence has to
   * live. `added` is pruned with it: a server removed during this visit goes
   * back to Add a server, which is both what the listing now says about it and
   * what the second sentence tells the operator to do. */
  function removed(name: string, cleared: boolean) {
    const label = LABELS[name] ?? name;
    setAdded((current) => current.filter((other) => other !== name));
    setRemoval(
      cleared
        ? `Removed ${label}.`
        : `Removed ${label}, but its credential could not be cleared and is ` +
          `still stored. Open ${label} again from Add a server to clear it.`,
    );
  }

  function toggle(name: string) {
    setOpen((current) =>
      current.includes(name)
        ? current.filter((other) => other !== name)
        : [...current, name],
    );
  }

  const switches = useMemo(() => switchValues(config), [config]);
  const rows = servers ?? [];
  // A server this deployment configured, or one the operator just picked from
  // Add a server -- that pick is what "adding" is, and the empty card it opens
  // is the form that configures it.
  const shown = rows.filter(
    (server) => server.configured || added.includes(server.name),
  );
  const addable = rows.filter(
    (server) => !server.configured && !added.includes(server.name),
  );
  const configured = new Set(
    rows.filter((server) => server.configured).map((server) => server.name),
  );
  // Both blocks stored, not merely both cards on screen: the map is a write
  // into Jellyfin's block that the route refuses unless the deployment holds
  // both servers, and a card opened from Add a server has nothing stored yet.
  const bothConfigured = configured.has("plex") && configured.has("jellyfin");

  return (
    <>
      {error !== null && (
        <section className="panel">
          <p className="page-error">{error}</p>
          <div className="config-actions">
            <button
              type="button"
              // The sentence goes with the press, so a read that takes its
              // time reads as something happening rather than as a dead
              // button under a failure that is still on screen.
              onClick={() => {
                setError(null);
                setAttempt((count) => count + 1);
              }}
            >
              Try again
            </button>
          </div>
        </section>
      )}
      {/* Above the accordions, because the card it is about has just gone
          from underneath it. */}
      {removal !== null && <p className="config-saved">{removal}</p>}
      {servers === null && error === null && <p className="muted">Loading…</p>}
      {/* Only reachable from a 200 whose body carried no server list, which
          `fetchServers` reads as none rather than letting the tab throw. A
          blank tab would look like a page that had not finished loading. */}
      {servers !== null && rows.length === 0 && error === null && (
        <p className="muted">This deployment listed no servers.</p>
      )}

      {shown.map((server) => (
        // The accordion's title is the only place this server is named: the
        // card inside carries its pills and its fields and no heading of its
        // own, so a server appears once in the outline rather than twice.
        <SettingsAccordion
          key={server.name}
          title={LABELS[server.name] ?? server.name}
          open={open.includes(server.name)}
          onToggle={() => toggle(server.name)}
        >
          <ServerCard
            server={server}
            switches={switches}
            revision={revision}
            pendingEdits={pendingEdits}
            onChanged={changed}
            onRemoved={(result) => removed(server.name, result.credential_cleared)}
          />
        </SettingsAccordion>
      ))}

      {addable.length > 0 && (
        <section className="panel">
          <h2>Add a server</h2>
          <p className="muted config-note">
            A server this deployment has no configuration for. Picking one
            opens an empty card for it; nothing is stored until that card is
            saved.
          </p>
          <div className="config-actions">
            {addable.map((server) => (
              <button
                key={server.name}
                type="button"
                onClick={() => {
                  // The sentence above was about this server not being here,
                  // and it is being put back.
                  setRemoval(null);
                  setAdded((current) => [...current, server.name]);
                  setOpen((current) =>
                    current.includes(server.name)
                      ? current
                      : [...current, server.name],
                  );
                }}
              >
                {`Add ${LABELS[server.name] ?? server.name}`}
              </button>
            ))}
          </div>
        </section>
      )}

      {/* Closed to begin with, whatever the cards are doing: opening it costs
          three requests, two of them out to a media server, and pairing is a
          job an operator comes to this tab for rather than one a card's
          credential leaves half done. */}
      {bothConfigured && (
        <LibraryMapPanel
          revision={revision}
          pendingEdits={pendingEdits}
          onChanged={changed}
          open={mapOpen}
          onToggle={() => setMapOpen((current) => !current)}
        />
      )}
    </>
  );
}
