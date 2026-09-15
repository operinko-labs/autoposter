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
import { ServerCard } from "./ServerCard";
import { SettingsAccordion } from "./SettingsAccordion";

const LABELS: Record<string, string> = { plex: "Plex", jellyfin: "Jellyfin" };

/** Every switch a card on this tab can show, as a dotted path into the served
 * configuration.
 *
 * One list for both cards rather than one per server: `ServerCard` keys its
 * save off its OWN table, so a path meant for the other server is read and
 * handed over but can never be sent, and a single list cannot fall out of step
 * with itself the way two would. */
const SWITCH_PATHS = [
  "badges.upload_to_plex",
  "operations.write_to_plex",
  "badges.upload_to_jellyfin",
  "operations.write_to_jellyfin",
  "jellyfin.replace_thumb_with_backdrop",
];

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

/** Which cards open by themselves (spec section 5).
 *
 * A fresh deployment opens Plex and leaves Jellyfin closed -- that is the pane
 * the Plex-only operator has always landed on, and two open cards is a long
 * column for somebody who runs one server. Once ANY server is configured,
 * cards open only when they need attention, which is exactly one shape: a
 * configured server with no credential, because that deployment will not boot.
 *
 * Extracted as a function rather than inlined so the rule can be tested
 * without rendering, and so the three cases are readable at once. */
export function cardsToOpen(servers: ServerRow[]): string[] {
  const configured = servers.filter((server) => server.configured);
  if (configured.length === 0) return ["plex"];
  return configured
    .filter((server) => server.credential_source === "unset")
    .map((server) => server.name);
}

export function ServersTab({
  config,
  revision,
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
  onChanged: () => Promise<void> | void;
}) {
  const [servers, setServers] = useState<ServerRow[] | null>(null);
  const [added, setAdded] = useState<string[]>([]);
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
   * the configuration is a separate read that has no reason to be skipped. */
  async function changed() {
    try {
      setServers(await fetchServers());
      setError(null);
    } catch (caught) {
      setError(refusalMessage(caught));
    }
    await onChanged();
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
            <button type="button" onClick={() => setAttempt((count) => count + 1)}>
              Try again
            </button>
          </div>
        </section>
      )}
      {servers === null && error === null && <p className="muted">Loading…</p>}

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
            onChanged={changed}
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
          onChanged={changed}
          open={mapOpen}
          onToggle={() => setMapOpen((current) => !current)}
        />
      )}
    </>
  );
}
