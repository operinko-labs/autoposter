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
 * server's own listing, THE STORED MAP INCLUDED: a stored pair naming a folder
 * Jellyfin no longer lists is dropped at seeding and said out loud, because a
 * row the dropdown cannot display is a row the panel would otherwise submit
 * behind the operator's back and have refused.
 *
 * SAME-NAMED LIBRARIES PAIR THEMSELVES. A row whose two names match is shown
 * as paired and the server drops it from what it stores, so the panel sends
 * every row it shows and the server decides which of them are worth keeping.
 * A map that comes out empty leaves no key behind at all. The one exception is
 * a folder another row's stored pair has already claimed: the index resolves
 * that folder to the other Plex library, so this row is genuinely unpaired and
 * is seeded that way.
 *
 * ONE JELLYFIN LIBRARY, ONE PLEX LIBRARY. The server refuses a second claim on
 * a folder, and this panel takes the option off the other rows' dropdowns so
 * the refusal is something an operator can only reach by racing themselves --
 * the server's sentence is still rendered if it arrives anyway.
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
 * validator's own 422 has no such field, and neither has the store's drop cap,
 * and both are about the body rather than about a row -- so they go to
 * `refusalMessage` with everything else. */
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

/** Whether this refusal is the store's drop cap, which asks to be overruled.
 *
 * Every pair is a document path of its own, so an operator clearing four rows
 * of a real map crosses the cap and is refused with a sentence naming an
 * action -- and, without the tick box this turns on, an action this panel
 * could not perform. Recognised by the sentence rather than by a code of its
 * own because the store has none: it is a 422 about `document`, the same shape
 * the request validator's is, and only the instruction tells them apart. */
function asksToBeOverruled(caught: unknown): boolean {
  if (!(caught instanceof ApiError) || caught.status !== 422) return false;
  if (!Array.isArray(caught.detail)) return false;
  return caught.detail.some(
    (entry) =>
      isPlainObject(entry) &&
      entry.path === "document" &&
      typeof entry.message === "string" &&
      entry.message.includes("confirm: true"),
  );
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
  const [dropped, setDropped] = useState<string[]>([]);
  const [readError, setReadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<MapProblem[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [overrule, setOverrule] = useState(false);
  const [asked, setAsked] = useState(false);
  const [busy, setBusy] = useState(false);

  // Read when the section is opened rather than at mount: three requests, two
  // of which reach out to a media server, for a section most visits to this
  // tab never open.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    // Everything the last opening left behind goes first, the rows included. A
    // success note standing over freshly-read rows claims a save that is no
    // longer what the screen shows, and per-row refusals outlive the values
    // that earned them; putting the rows back to "not read yet" is also what
    // puts the loading line back while these three are in flight.
    setPlex(null);
    setJellyfin([]);
    setPairs({});
    setDropped([]);
    setReadError(null);
    setError(null);
    setProblems([]);
    setNote(null);
    setOverrule(false);
    setAsked(false);
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
      // Folders the stored map has already given to a Plex library. A second
      // row cannot have one: the index resolves a folder to exactly one Plex
      // library, so the row that did not get it is unpaired however its name
      // reads.
      const claimedByStored = new Set(Object.values(stored));
      const seeded: Record<string, string> = {};
      const lost: string[] = [];
      for (const row of plexRows) {
        const pair = stored[row.name];
        if (pair !== undefined) {
          if (jellyfinNames.has(pair)) {
            seeded[row.name] = pair;
          } else {
            // Dropped rather than carried: the dropdown has no option for it,
            // so a row that kept it would read as unpaired and submit the
            // vanished name anyway -- and be refused for naming a library
            // Jellyfin does not list. An ordinary rename lands here, and that
            // is the repair this panel is for, so it is said rather than
            // quietly fixed.
            seeded[row.name] = "";
            lost.push(
              `${row.name} was paired with a Jellyfin library called ${pair}, ` +
                "which Jellyfin no longer lists, so this row is now unpaired.",
            );
          }
          continue;
        }
        // A row with no stored pair shows its OWN name when Jellyfin lists one
        // by that name: that is the pairing, and showing it as "not paired"
        // would invite a fix for something that is not broken. Unless another
        // row's stored pair already holds that folder, in which case this row
        // really is unpaired.
        seeded[row.name] =
          jellyfinNames.has(row.name) && !claimedByStored.has(row.name)
            ? row.name
            : "";
      }
      setPlex(plexRows);
      setJellyfin(jellyfinRows);
      setPairs(seeded);
      setDropped(lost);
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
    setAsked(false);
    try {
      // Only the rows that name a Jellyfin library. An unpaired row is the
      // absence of a pair, and the route stores the whole map it is sent, so
      // sending `""` would store a pair onto a library called nothing.
      const filled = Object.fromEntries(
        Object.entries(pairs).filter(([, value]) => value !== ""),
      );
      await saveLibraryMap(filled, revision, overrule);
      await onChanged();
      // The map's leaf paths do land on the restart list: the Jellyfin client
      // and its library index are built once at startup from the stored map,
      // so a saved map is genuinely not in force until the restart.
      setNote(`Saved the library map. ${RESTART_NOTE}`);
    } catch (caught) {
      const refused = mapProblems(caught);
      if (refused.length > 0) setProblems(refused);
      else setError(refusalMessage(caught));
      setAsked(asksToBeOverruled(caught));
    } finally {
      // Dropped whether the save landed or was refused, the way the two other
      // panels offering this tick do it: it authorises the press that was
      // made, never the next one.
      setOverrule(false);
      setBusy(false);
    }
  }

  const writable = revision !== null;
  const read = plex !== null;
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
        so those rows are not stored and cannot be cleared. Every name here
        comes from the servers&apos; own listings, and each Jellyfin library can
        be paired with one Plex library. A server added here is not mapped
        until this deployment restarts.
      </p>
      {readError !== null && <p className="page-error">{readError}</p>}
      {dropped.map((sentence) => (
        <p className="muted config-note" key={sentence}>
          {sentence}
        </p>
      ))}
      {error !== null && <p className="page-error">{error}</p>}
      {problems.map((problem) => (
        <p
          className="page-error"
          key={`${problem.library}: ${problem.message}`}
        >{`${problem.library}: ${problem.message}`}</p>
      ))}
      {note !== null && <p className="config-saved">{note}</p>}
      {!read && <p className="muted">Loading…</p>}
      {/* Only when the read itself succeeded: an empty list after a refused
          read is this panel's fallback rather than Plex's answer, and saying
          Plex listed nothing would be putting words in its mouth. */}
      {read && plex.length === 0 && readError === null && (
        <p className="muted">Plex listed no libraries, so there is nothing to pair.</p>
      )}
      {(plex ?? []).map((row) => {
        const value = pairs[row.name] ?? "";
        const claimed = claimedBesides(row.name);
        return (
          <div className="config-row" key={row.id}>
            <span className="config-key">{row.name}</span>
            <span className="config-value">
              <select
                aria-label={row.name}
                value={value}
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
                    // would be offering a refusal. This row's OWN value is
                    // never taken from it, or a stored map that pairs one
                    // folder twice would grey out the selection showing in the
                    // box and leave no way back to it.
                    disabled={claimed.has(option.name) && option.name !== value}
                  >
                    {option.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                aria-label={`Clear ${row.name}`}
                // A same-named row cannot be cleared: the two servers pair it
                // whether the map says so or not, so clearing it would look
                // like an action and come back at the next read. The note
                // above says why.
                disabled={busy || value === "" || value === row.name}
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
      {/* Offered only once the store has refused for this reason, so the
          ordinary save stays one press and this one is deliberate. */}
      {asked && (
        <label className="config-confirm">
          <input
            type="checkbox"
            checked={overrule}
            onChange={(event) => setOverrule(event.target.checked)}
          />
          Allow this to remove settings I have saved
        </label>
      )}
      <div className="config-actions">
        <button
          type="button"
          disabled={busy || !writable || !read}
          onClick={() => void save()}
        >
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
