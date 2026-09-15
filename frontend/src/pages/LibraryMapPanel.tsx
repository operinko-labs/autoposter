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
 * AND ONLY THE LIBRARIES THIS SERVICE WALKS. A listing carries every folder a
 * server holds, but the route validates each half of a pair against the movie
 * and show libraries alone, so the two listings are filtered by kind before
 * anything is seeded from them: a photo section offered as a row, or a music
 * folder offered on a dropdown, is a pairing the route refuses and an index
 * that would resolve that library to nothing.
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
import {
  fetchLibraries,
  restartWaiting,
  saveLibraryMap,
  type LibraryRow,
} from "../api/servers";
import type { ConfigResponse } from "../api/types";
import { PENDING_EDITS_NOTE } from "./RestartBanner";
import { SettingsAccordion } from "./SettingsAccordion";

/** What the empty option says, and what it means: this row sends nothing. */
const NOT_PAIRED = "(not paired)";

/** What each server calls the two library kinds this service walks, mirrored
 * from `INDEXED_KINDS` in `src/autoposter/api/servers.py` the way `SWITCHES`
 * mirrors `SERVER_SWITCHES`.
 *
 * The listings both servers answer with are unfiltered -- every folder they
 * hold, with its own kind -- but the route validates each half of a pair
 * against the listing FILTERED by these, and refuses anything else with "has
 * a library called that, but it is not a movie or show library". A row for a
 * photo library, or a music folder on a dropdown, is therefore a refusal this
 * panel would be offering: the pairing is not merely unsupported, it maps a
 * library onto nothing, and every item in it stays unresolved forever. A
 * stored pair naming one falls into the "Jellyfin no longer lists" path
 * below, which says so. */
const INDEXED_KINDS: Record<string, Set<string>> = {
  plex: new Set(["movie", "show"]),
  jellyfin: new Set(["movies", "tvshows"]),
};

/** One server's listing, with the folders this service would never walk taken
 * out. A kind this table does not name is dropped rather than kept: the route
 * refuses it, so keeping it would put a control on screen for a pairing that
 * cannot be made. */
function indexable(rows: LibraryRow[], name: string): LibraryRow[] {
  const kinds = INDEXED_KINDS[name];
  return kinds === undefined ? rows : rows.filter((row) => kinds.has(row.kind));
}

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
  pendingEdits = false,
  onChanged,
  open,
  onToggle,
}: {
  /** The configuration revision the page was served. This write is checked
   * against it, so a page served none cannot save: the route refuses a body
   * without one, and that refusal is the only thing standing between a
   * concurrent settings save and a silent revert of it. */
  revision: string | null;
  /** Whether the page is holding an edit nobody has stored. This save asks
   * the page to re-read, and that re-read re-seeds its editor from the
   * server, so the press is refused rather than allowed to discard it. */
  pendingEdits?: boolean;
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
      const [config, allPlexRows, allJellyfinRows] = await Promise.all([
        settle<ConfigResponse>(apiFetch<ConfigResponse>("/api/config"), {}),
        settle<LibraryRow[]>(fetchLibraries("plex", {}), []),
        settle<LibraryRow[]>(fetchLibraries("jellyfin", {}), []),
      ]);
      if (cancelled) return;
      // Both listings, before anything is seeded from them: a photo section
      // is a row the route refuses, and a music folder is an option on every
      // dropdown that cannot be chosen honestly.
      const plexRows = indexable(allPlexRows, "plex");
      const jellyfinRows = indexable(allJellyfinRows, "jellyfin");
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
      const saved = await saveLibraryMap(filled, revision, overrule);
      await onChanged();
      // Which Jellyfin folder holds a Plex library's items decides which
      // items Jellyfin is owed, so the service starts the backlog itself --
      // and the operator who is not told goes looking for the button that
      // started it.
      const said = "Saved the library map. This also starts a catch-up for Jellyfin.";
      // Then what the response says rather than a sentence of this panel's
      // own: the map's leaf paths do reach the restart list, because the
      // Jellyfin client and its library index are built once at startup from
      // the stored map -- but the route is the contract of record, and a save
      // that left nothing waiting must not claim otherwise.
      setNote(
        restartWaiting(saved.restart_required) ? `${said} ${RESTART_NOTE}` : said,
      );
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
        comes from the servers&apos; own listings, and only their movie and
        show libraries appear: those are the ones this service walks. Each
        Jellyfin library can be paired with one Plex library. A server added
        here is not mapped until this deployment restarts.
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
      {/* One line for the one control it refuses, in the wording the restart
          banner and the two System-tab panels use. */}
      {pendingEdits && <p className="muted config-note">{PENDING_EDITS_NOTE}</p>}
      <div className="config-actions">
        <button
          type="button"
          disabled={busy || !writable || !read || pendingEdits}
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
