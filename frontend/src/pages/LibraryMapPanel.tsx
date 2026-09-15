/** The Servers tab's library map: one Plex library per row, paired with the
 * Jellyfin library that holds the same items.
 *
 * BOTH LISTS ARE READ LIVE FROM THE SERVERS THEMSELVES and the control is a
 * dropdown rather than a text field. That is a requirement rather than a
 * convenience: whether an item is missing from Jellyfin is decided by looking
 * for its library's map partner among the folders Jellyfin lists, so a map
 * naming a library nothing carries would leave every item in it permanently
 * pending. A name typed by hand cannot be checked against anything, so there
 * is no field here to type one into -- every name on this panel came from a
 * server's own listing.
 *
 * SAME-NAMED LIBRARIES PAIR THEMSELVES. A row whose two names match is shown
 * as paired and the server drops it from what it stores, so the panel sends
 * every row it shows and the server decides which of them are worth keeping.
 * A map that comes out empty leaves no key behind at all.
 *
 * ONE JELLYFIN LIBRARY, ONE PLEX LIBRARY. The server refuses a second claim on
 * a folder, and this panel takes the option off the other rows' dropdowns so
 * the refusal is something an operator can only reach by racing themselves --
 * the server's sentence is still rendered if it arrives.
 *
 * THE READS ARE INDEPENDENT OF EACH OTHER. A server that refuses its listing
 * puts its own sentence on the panel and leaves the rest of it working, rather
 * than replacing the whole map with one server's failure.
 */
import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
import { isPlainObject, readPath, refusalMessage, RESTART_NOTE } from "../api/overrides";
import { fetchLibraries, saveLibraryMap, type LibraryRow } from "../api/servers";
import type { ConfigResponse } from "../api/types";
import { SettingsAccordion } from "./SettingsAccordion";

/** What the empty option says, and what it means: this row sends nothing. */
const NOT_PAIRED = "(not paired)";

/** One refused row, as the route reports it.
 *
 * The library name arrives in a field of its OWN rather than inside the dotted
 * path, because a library name is the operator's data and Plex allows a dot in
 * one -- so `fieldErrors` (which keys by path) would collapse every refused
 * row of one save onto the single section path and the operator would be told
 * which mistakes were made without being told which rows made them. */
interface MapProblem {
  library: string;
  message: string;
}

/** The refused rows in a 422, or an empty list for a refusal of another shape.
 *
 * An entry without a `library` is left out rather than guessed at: the request
 * validator's own 422 has no such field, and it is about the body rather than
 * about a row, so it goes to `refusalMessage` with everything else. */
function mapProblems(caught: unknown): MapProblem[] {
  if (!(caught instanceof ApiError) || caught.status !== 422) return [];
  if (!Array.isArray(caught.detail)) return [];
  const rows: MapProblem[] = [];
  for (const entry of caught.detail) {
    if (!isPlainObject(entry)) continue;
    if (typeof entry.library !== "string" || typeof entry.message !== "string") {
      continue;
    }
    rows.push({ library: entry.library, message: entry.message });
  }
  return rows;
}

/** The stored map inside a served configuration.
 *
 * Read defensively for the reason every other listing on this tab is: the
 * panel maps over this inside its render, so a document whose `library_map` is
 * something other than a table of names would throw where the panel's own
 * error line cannot catch it. */
function storedMap(config: ConfigResponse): Record<string, string> {
  const found = readPath(config, "jellyfin.library_map");
  if (!isPlainObject(found)) return {};
  const pairs: Record<string, string> = {};
  for (const [plexName, jellyfinName] of Object.entries(found)) {
    if (typeof jellyfinName === "string") pairs[plexName] = jellyfinName;
  }
  return pairs;
}

export function LibraryMapPanel({
  revision,
  onChanged,
  open,
  onToggle,
}: {
  /** The configuration revision the page was served. This write is checked
   * against it, so a page served none cannot save: the route refuses a body
   * without one, and that refusal is the only thing standing between a
   * concurrent settings save and a silent revert of it. */
  revision: string | null;
  onChanged: () => Promise<void> | void;
  /** Whether this accordion is the open one. Held by the tab, the way the
   * System tab holds the secrets accordion's, so the page keeps its one rule:
   * one section open at a time. */
  open: boolean;
  onToggle: () => void;
}) {
  const [plex, setPlex] = useState<LibraryRow[] | null>(null);
  const [jellyfin, setJellyfin] = useState<LibraryRow[]>([]);
  const [pairs, setPairs] = useState<Record<string, string>>({});
  const [readError, setReadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<MapProblem[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Read when the section is opened rather than at mount: three requests, two
  // of which reach out to a media server, for a section most visits to this
  // tab never open.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const failures: string[] = [];
    // Each read is settled on its own, so one server's refusal does not take
    // the other server's listing and the stored map down with it.
    const settle = async <T,>(work: Promise<T>, fallback: T): Promise<T> => {
      try {
        return await work;
      } catch (caught) {
        // The server's own sentence, whatever shape it arrived in.
        failures.push(refusalMessage(caught));
        return fallback;
      }
    };
    void (async () => {
      const [config, plexRows, jellyfinRows] = await Promise.all([
        settle<ConfigResponse>(apiFetch<ConfigResponse>("/api/config"), {}),
        settle<LibraryRow[]>(fetchLibraries("plex", {}), []),
        settle<LibraryRow[]>(fetchLibraries("jellyfin", {}), []),
      ]);
      if (cancelled) return;
      const stored = storedMap(config);
      const jellyfinNames = new Set(jellyfinRows.map((row) => row.name));
      setPlex(plexRows);
      setJellyfin(jellyfinRows);
      // A row with no stored pair shows its OWN name when Jellyfin lists one
      // by that name: that is the pairing, and showing it as "not paired"
      // would invite an operator to fix something that is not broken.
      setPairs(
        Object.fromEntries(
          plexRows.map((row) => [
            row.name,
            stored[row.name] ?? (jellyfinNames.has(row.name) ? row.name : ""),
          ]),
        ),
      );
      setReadError(failures.length === 0 ? null : failures.join(" "));
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  async function save() {
    if (revision === null) return;
    setBusy(true);
    setError(null);
    setProblems([]);
    setNote(null);
    try {
      // Only the rows that name a Jellyfin library. An unpaired row is the
      // absence of a pair, and the route stores the whole map it is sent, so
      // sending `""` would store a pair onto a library called nothing.
      const filled = Object.fromEntries(
        Object.entries(pairs).filter(([, value]) => value !== ""),
      );
      await saveLibraryMap(filled, revision);
      await onChanged();
      // The map's leaf paths do land on the restart list: the Jellyfin client
      // and its library index are built once at startup from the stored map,
      // so a saved map is genuinely not in force until the restart.
      setNote(`Saved the library map. ${RESTART_NOTE}`);
    } catch (caught) {
      const refused = mapProblems(caught);
      if (refused.length > 0) setProblems(refused);
      else setError(refusalMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  const writable = revision !== null;
  /** The Jellyfin libraries some OTHER row has already claimed. */
  function claimedBesides(plexName: string): Set<string> {
    const claimed = new Set<string>();
    for (const [key, value] of Object.entries(pairs)) {
      if (key !== plexName && value !== "") claimed.add(value);
    }
    return claimed;
  }

  return (
    <SettingsAccordion title="Library map" open={open} onToggle={onToggle}>
      <p className="muted config-note">
        Pair each Plex library with the Jellyfin library that holds the same
        items. Libraries called the same thing on both servers pair themselves,
        so those rows are not stored. Every name here comes from the servers&apos;
        own listings, and each Jellyfin library can be paired with one Plex
        library.
      </p>
      {readError !== null && <p className="page-error">{readError}</p>}
      {error !== null && <p className="page-error">{error}</p>}
      {problems.map((problem) => (
        <p
          className="page-error"
          key={`${problem.library}: ${problem.message}`}
        >{`${problem.library}: ${problem.message}`}</p>
      ))}
      {note !== null && <p className="config-saved">{note}</p>}
      {plex === null && <p className="muted">Loading…</p>}
      {/* Only when the read itself succeeded: an empty list after a refused
          read is this panel's fallback rather than Plex's answer, and saying
          Plex listed nothing would be putting words in its mouth. */}
      {plex !== null && plex.length === 0 && readError === null && (
        <p className="muted">Plex listed no libraries, so there is nothing to pair.</p>
      )}
      {(plex ?? []).map((row) => {
        const claimed = claimedBesides(row.name);
        return (
          <div className="config-row" key={row.id}>
            <span className="config-key">{row.name}</span>
            <span className="config-value">
              <select
                aria-label={row.name}
                value={pairs[row.name] ?? ""}
                disabled={busy}
                onChange={(event) =>
                  setPairs((current) => ({
                    ...current,
                    [row.name]: event.target.value,
                  }))
                }
              >
                <option value="">{NOT_PAIRED}</option>
                {jellyfin.map((option) => (
                  <option
                    key={option.id}
                    value={option.name}
                    // Taken by another row. The server refuses a second claim
                    // on one folder, and a dropdown that offered the choice
                    // would be offering a refusal.
                    disabled={claimed.has(option.name)}
                  >
                    {option.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                aria-label={`Clear ${row.name}`}
                disabled={busy || (pairs[row.name] ?? "") === ""}
                onClick={() =>
                  setPairs((current) => ({ ...current, [row.name]: "" }))
                }
              >
                Clear
              </button>
            </span>
          </div>
        );
      })}
      <div className="config-actions">
        <button type="button" disabled={busy || !writable} onClick={() => void save()}>
          Save the map
        </button>
      </div>
      {!writable && (
        <p className="muted config-note">
          This page was served no configuration revision, which this write is
          checked against, so reload the page before saving.
        </p>
      )}
    </SettingsAccordion>
  );
}
