import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
// The overrides document and its helpers are shared with the settings page and
// the sibling panels -- see `api/overrides.ts` for why a second copy of the
// seeding rule would be a bug rather than a duplication. Nothing in this file
// is added to that module: every helper it needs is already path-agnostic.
import {
  documentFromConfig,
  fieldErrors,
  isPlainObject,
  readPath,
  revisionFromConfig,
  saveBody,
  STALE_SAVE_NOTE,
  withPath,
  withoutPath,
} from "../api/overrides";
import { DefinitionEditor, PLAYLIST_DEFINITIONS } from "./DefinitionEditor";
import type {
  ConfigResponse,
  ConfigSaveResponse,
  OverridesDocument,
  ParsedSourceResponse,
  PlaylistDefinitionSummary,
  PlaylistDefinitionsListingResponse,
} from "../api/types";
// The sibling panel's stylesheet, deliberately: this panel is that panel's
// shape with one path changed, so a second copy of its rules would be two
// files to keep in step for no visual difference.
import "./custom-collections.css";

/** Where every write here lands. The overrides layer replaces a list
 * WHOLESALE, so this panel always writes the complete override list -- and
 * builds it only from `overrideList` below, never from the whole config and
 * never from the server's listing rows. The listing is a projection; a
 * playlist entry rebuilt from it would drop the `schedule` the form does not
 * show, and an entry the mounted file supplies would be pinned at today's YAML
 * values against every future edit of that file. */
const DEFINITIONS_PATH = "playlists.definitions";

const GUARD_ID = "playlists-file-guard";

/** What Read URL actually verifies -- shape, never existence. The endpoint is
 * the collections one on purpose: `source_urls.parse_source` names no config
 * section, so a second copy for playlists would be a second parser to keep in
 * step with MDBList's and IMDb's URL shapes. */
const SHAPE_ONLY_NOTE =
  "The URL is checked for shape only — whether the list exists is discovered " +
  "when the reconcile pass next runs it, and a source that fails leaves its " +
  "playlist untouched rather than emptying it.";

const REMOVE_NOTE =
  "Removing a definition only stops the pass building it — the playlist " +
  "already in Plex follows playlists.delete_unconfigured: reported as an " +
  "orphan by default, deleted only when that setting says so.";

const EDIT_NOTE =
  "Edit changes a definition's own fields. The builder and its parameters are " +
  "not editable — change those by removing the definition and creating it " +
  "again from a URL. Renaming leaves the playlist already in Plex under its " +
  "old title, which playlists.delete_unconfigured then treats as an orphan.";

/** What a preset row is, and what it is not. Said here rather than left to the
 * missing buttons, because a row with no controls otherwise reads as a bug. */
const PRESET_NOTE =
  "Preset rows come from playlists.presets and are stored in no document, so " +
  "they cannot be edited or removed here — switch one off by removing its key " +
  "from playlists.presets on the settings page. To customise one instead, " +
  "create a definition below with the same title: yours is built and the " +
  "preset stands aside.";

const FILE_ROWS_NOTE =
  "Creating here is refused while any definition below comes from the mounted " +
  "config file: the file's definitions do not merge with definitions created " +
  "here — the first save here would store an overrides list that replaces the " +
  "file's for as long as it exists, so every row marked config file would " +
  "quietly stop being built. Two ways forward: keep managing definitions in " +
  "the config file (Read URL below still resolves a pasted URL to the builder " +
  "and params to write there), or move those rows into this form once and " +
  "empty the file's definitions list.";

const APPLIES_NOTE =
  "The change is live in the running process, and the playlist itself appears " +
  "at the next reconcile — the playlists pass runs with the collections one.";

function overrideList(stored: OverridesDocument): unknown[] {
  const value = readPath(stored, DEFINITIONS_PATH);
  return Array.isArray(value) ? value : [];
}

function documentForCreate(
  stored: OverridesDocument,
  entry: Record<string, unknown>,
): OverridesDocument {
  return withPath(stored, DEFINITIONS_PATH, [...overrideList(stored), entry]);
}

/** The stored document minus the override entry at `ordinal`. Removing the
 * last entry drops the key instead of writing `[]`: the key going away is the
 * overrides contract's revert, and it hands the decision back to whatever the
 * mounted file lists. */
function documentForRemove(
  stored: OverridesDocument,
  ordinal: number,
): OverridesDocument {
  const remaining = overrideList(stored).filter((_, at) => at !== ordinal);
  return remaining.length === 0
    ? withoutPath(stored, DEFINITIONS_PATH)
    : withPath(stored, DEFINITIONS_PATH, remaining);
}

/** A splice, never a rebuild: the entries either side are the same object
 * references that came out of `overrideList`, so an edit cannot perturb a
 * sibling even by accident. */
function documentForEdit(
  stored: OverridesDocument,
  ordinal: number,
  entry: Record<string, unknown>,
): OverridesDocument {
  const next = overrideList(stored).map((item, at) => (at === ordinal ? entry : item));
  return withPath(stored, DEFINITIONS_PATH, next);
}

/** A listing row's position within the stored override list: its index among
 * the override-provenance rows. Preset and file rows are skipped by the same
 * count, which is why this counts rather than using the raw index -- a preset
 * row genuinely sits in the listing ahead of every stored entry. */
function overrideOrdinal(
  definitions: PlaylistDefinitionSummary[],
  index: number,
): number {
  return (
    definitions.slice(0, index + 1).filter((row) => row.provenance === "override")
      .length - 1
  );
}

function refusalMessage(caught: unknown): string {
  if (caught instanceof ApiError && Array.isArray(caught.detail)) {
    const messages = Object.values(fieldErrors(caught.detail));
    if (messages.length > 0) return messages.join("; ");
  }
  return (caught as Error).message;
}

function badgeLabel(provenance: PlaylistDefinitionSummary["provenance"]): string {
  if (provenance === "file") return "config file";
  if (provenance === "preset") return "preset";
  return "override";
}

/** The config-defined playlist definitions, and the form that creates one from
 * a pasted list URL.
 *
 * `CustomCollectionsPanel`'s shape, one section along, and copied rather than
 * abstracted: there is no existing abstraction over "a list-of-objects config
 * section with a CRUD panel", and the codebase's own rule is to extract only
 * when a third such section appears (recon 2.4). What IS shared is everything
 * that would be a bug in duplicate: the overrides protocol (`api/overrides.ts`)
 * and the edit form (`DefinitionEditor`, parameterised rather than copied).
 *
 * Three provenances, three postures. An `override` row is this panel's to edit
 * and remove. A `file` row is rendered with a badge and no controls, and its
 * presence disables Create outright -- an overrides list replaces the file's
 * wholesale. A `preset` row is config the server expands: stored nowhere, so
 * nothing here can splice it, and switched off by name on the settings page. */
export function PlaylistsPanel() {
  const [listing, setListing] = useState<PlaylistDefinitionsListingResponse | null>(
    null,
  );
  const [stored, setStored] = useState<OverridesDocument>({});
  const [storedRevision, setStoredRevision] = useState<string | null>(null);
  const [descriptions, setDescriptions] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [parsed, setParsed] = useState<ParsedSourceResponse | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});

  const [busy, setBusy] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ConfigSaveResponse | null>(null);
  const [editing, setEditing] = useState<number | null>(null);

  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const adopt = useCallback(
    (definitions: PlaylistDefinitionsListingResponse, config: ConfigResponse) => {
      setListing(definitions);
      setStored(documentFromConfig(config));
      setStoredRevision(revisionFromConfig(config));
      const described = config.field_descriptions;
      setDescriptions(
        isPlainObject(described)
          ? Object.fromEntries(
              Object.entries(described).map(([key, value]) => [key, String(value)]),
            )
          : {},
      );
      setChosen(
        Object.fromEntries(definitions.libraries.map((name) => [name, true])),
      );
      // A fresh listing may renumber the ordinals; an open form pointing at the
      // old numbering would write the edit into the wrong entry.
      setEditing(null);
    },
    [],
  );

  const reload = useCallback(async () => {
    const [definitions, config] = await Promise.all([
      apiFetch<PlaylistDefinitionsListingResponse>("/api/playlists/definitions"),
      apiFetch<ConfigResponse>("/api/config"),
    ]);
    if (live.current) adopt(definitions, config);
  }, [adopt]);

  useEffect(() => {
    reload().catch((caught: Error) => {
      if (live.current) setLoadError(caught.message);
    });
  }, [reload]);

  if (loadError !== null) {
    return (
      <section className="panel custom-collections-panel">
        <h2>Playlists</h2>
        <p className="page-error">{loadError}</p>
      </section>
    );
  }

  if (listing === null) {
    return (
      <section className="panel custom-collections-panel">
        <h2>Playlists</h2>
        <p className="muted">Loading…</p>
      </section>
    );
  }

  const fileRows = listing.definitions.some((row) => row.provenance === "file");
  const presetRows = listing.definitions.some((row) => row.provenance === "preset");
  const chosenNames = listing.libraries.filter((name) => chosen[name]);
  const allChosen = chosenNames.length === listing.libraries.length;
  const complete = parsed !== null && title.trim() !== "" && chosenNames.length > 0;
  const ready = complete && !fileRows;

  function touch() {
    setResult(null);
    setReloadError(null);
    setErrors({});
    setSaveError(null);
  }

  function entry(): Record<string, unknown> {
    return {
      title: title.trim(),
      builder: parsed!.builder,
      params: parsed!.params,
      // All boxes checked = the key omitted = the definition's own "every
      // library in the playlists scope" default (`None`). An explicit full list
      // would freeze today's library names into the definition.
      ...(allChosen ? {} : { libraries: chosenNames }),
    };
  }

  async function parse() {
    setBusy("parsing");
    setParsed(null);
    setParseError(null);
    touch();
    try {
      // The collections endpoint, deliberately: it is builder-agnostic. A
      // paste that resolves to a builder a PLAYLIST cannot use is not refused
      // here -- it is refused by the schema on save, and the 422 lands in the
      // error list below against the path it names.
      const response = await apiFetch<ParsedSourceResponse>(
        "/api/collections/parse-source",
        { method: "POST", body: JSON.stringify({ url }) },
      );
      if (live.current) setParsed(response);
    } catch (caught) {
      if (live.current) setParseError(refusalMessage(caught));
    } finally {
      if (live.current) setBusy(null);
    }
  }

  /** One PUT for create, edit and remove -- the sibling panels' idiom
   * verbatim: store, then re-read OUTSIDE the try, because a failed re-read
   * after a successful store is staleness, not an unsaved change. */
  async function put(document: OverridesDocument, key: string) {
    setBusy(key);
    touch();
    let saved = false;
    let stale = false;
    try {
      const response = await apiFetch<ConfigSaveResponse>("/api/config/overrides", {
        method: "PUT",
        body: saveBody(document, storedRevision),
      });
      if (live.current) setResult(response);
      saved = true;
    } catch (caught) {
      if (live.current) {
        if (caught instanceof ApiError && caught.status === 409) {
          setSaveError(STALE_SAVE_NOTE);
          stale = true;
        } else if (caught instanceof ApiError && caught.status === 422) {
          setErrors(fieldErrors(caught.detail));
          setSaveError("The server rejected this change.");
        } else {
          setSaveError((caught as Error).message);
        }
      }
    }
    if (saved || stale) {
      if (live.current && saved && key === "saving") {
        setTitle("");
        setUrl("");
        setParsed(null);
        setParseError(null);
      }
      try {
        await reload();
      } catch (caught) {
        if (live.current) {
          setReloadError(
            `${saved ? "Saved, but" : "Nothing was saved, and"} the panel ` +
              `could not be re-read (${(caught as Error).message}). ` +
              "What is shown may be stale — reload the page.",
          );
        }
      }
    }
    if (live.current) setBusy(null);
  }

  return (
    <section className="panel custom-collections-panel">
      <h2>Playlists</h2>

      <p className="muted custom-note">
        The config's own <span className="mono">playlists.definitions</span> —
        what the playlists pass builds beyond the presets. Create one from a
        list URL below; it is stored as a config override, like the settings
        page's edits.
      </p>
      <p className="muted custom-note">{REMOVE_NOTE}</p>
      <p className="muted custom-note">{EDIT_NOTE}</p>
      {presetRows && <p className="muted custom-note">{PRESET_NOTE}</p>}
      {listing.preset_conflicts.length > 0 && (
        <p
          className="muted custom-note"
          role="status"
          aria-label="Displaced presets"
        >
          {"Not built, because a definition below already builds the same " +
            "title: "}
          {listing.preset_conflicts
            .map((conflict) => `${conflict.key} (${conflict.title})`)
            .join("; ")}
          {". Remove the key from playlists.presets to stop it being reported."}
        </p>
      )}
      {fileRows && (
        <p className="muted custom-note custom-file-note" id={GUARD_ID}>
          {FILE_ROWS_NOTE}
        </p>
      )}

      {listing.definitions.length === 0 ? (
        <p className="empty">No playlists are configured — create one below.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Builder</th>
                <th>Params</th>
                <th>Libraries</th>
                <th>Source</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {listing.definitions.map((row, index) => (
                <tr key={`${index}-${row.title}`}>
                  <td className="cell-title">{row.title}</td>
                  <td className="mono">{row.builder}</td>
                  <td className="mono custom-params">{JSON.stringify(row.params)}</td>
                  <td className="muted">
                    {row.libraries === null ? "all" : row.libraries.join(", ")}
                  </td>
                  <td>
                    <span className={`custom-badge custom-${row.provenance}`}>
                      {badgeLabel(row.provenance)}
                    </span>
                    {row.preset_key !== null && (
                      <span className="mono"> {row.preset_key}</span>
                    )}
                  </td>
                  <td className="custom-row-actions">
                    {/* Only override rows. A file row's missing controls are
                        the freezing guard made visible; a preset row's are the
                        plain truth that there is no stored entry to splice. */}
                    {row.provenance === "override" && (
                      <>
                        <button
                          type="button"
                          aria-label={`Edit ${row.title}`}
                          disabled={busy !== null}
                          onClick={() => {
                            touch();
                            setEditing(overrideOrdinal(listing.definitions, index));
                          }}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          aria-label={`Remove ${row.title}`}
                          disabled={busy !== null}
                          onClick={() =>
                            void put(
                              documentForRemove(
                                stored,
                                overrideOrdinal(listing.definitions, index),
                              ),
                              `removing-${index}`,
                            )
                          }
                        >
                          {busy === `removing-${index}` ? "Removing…" : "Remove"}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing !== null && overrideList(stored)[editing] !== undefined && (
        <DefinitionEditor
          key={editing}
          entry={overrideList(stored)[editing] as Record<string, unknown>}
          libraries={listing.libraries}
          descriptions={descriptions}
          busy={busy !== null}
          kind={PLAYLIST_DEFINITIONS}
          onSave={(entry) =>
            void put(documentForEdit(stored, editing, entry), "editing")
          }
          onCancel={() => setEditing(null)}
        />
      )}

      <h3 className="custom-form-title">Create from a list URL</h3>
      <p className="muted custom-note">{SHAPE_ONLY_NOTE}</p>

      <div className="custom-form">
        <label className="custom-field">
          <span>Title</span>
          <input
            type="text"
            value={title}
            aria-label="Playlist title"
            onChange={(event) => {
              touch();
              setTitle(event.target.value);
            }}
          />
        </label>
        <label className="custom-field custom-url">
          <span>Source URL</span>
          <input
            type="text"
            value={url}
            placeholder="https://www.imdb.com/list/ls… — or an MDBList, TMDb or TVDb list URL"
            aria-label="Source URL"
            onChange={(event) => {
              touch();
              setParsed(null);
              setParseError(null);
              setUrl(event.target.value);
            }}
          />
        </label>
        <button
          type="button"
          disabled={busy !== null || url.trim() === ""}
          onClick={() => void parse()}
        >
          {busy === "parsing" ? "Reading…" : "Read URL"}
        </button>
      </div>

      {parseError !== null && (
        <p className="custom-parse-error" role="alert">
          {parseError}
        </p>
      )}
      {parsed !== null && (
        // Named, like the displaced-presets note above it: a builder name is
        // not unique on this page -- a listing row renders the same string in
        // its Builder column -- so a test waiting for the parse must be able
        // to scope to this region rather than to the text.
        <p className="custom-parsed" role="status" aria-label="Parsed source">
          Builder: <span className="mono">{parsed.builder}</span>
          {" — "}
          {parsed.display_note}
        </p>
      )}

      <fieldset className="custom-libraries">
        <legend className="muted">
          Libraries — all checked resolves members from every configured library
        </legend>
        {listing.libraries.map((name) => (
          <label key={name} className="custom-library">
            <input
              type="checkbox"
              checked={chosen[name] ?? false}
              onChange={(event) => {
                touch();
                setChosen((previous) => ({
                  ...previous,
                  [name]: event.target.checked,
                }));
              }}
            />
            <span>{name}</span>
          </label>
        ))}
      </fieldset>

      <div className="custom-actions">
        <button
          type="button"
          disabled={busy !== null || !ready}
          aria-describedby={fileRows ? GUARD_ID : undefined}
          onClick={() => void put(documentForCreate(stored, entry()), "saving")}
        >
          {busy === "saving" ? "Creating…" : "Create"}
        </button>
      </div>

      {saveError !== null && <p className="page-error">{saveError}</p>}
      {Object.entries(errors).length > 0 && (
        <ul className="custom-errors">
          {Object.entries(errors).map(([path, message]) => (
            <li key={path}>
              <span className="mono">{path}</span>{" "}
              <span className="custom-error-message">{message}</span>
            </li>
          ))}
        </ul>
      )}

      {/* Outside the `result` gate, not inside it: a refused save never sets
          `result`, so a re-read failure reported in there would be invisible on
          exactly the path where the panel is most misleading. */}
      {reloadError !== null && <p className="custom-stale">{reloadError}</p>}

      {result !== null && (
        <div className="custom-saved" role="status">
          <p>{`Saved. Config ${result.version_before} → ${result.version_after}.`}</p>
          <p className="muted">{APPLIES_NOTE}</p>
          {result.restart_required.length > 0 && (
            <p className="muted">
              {`Needs a restart: ${result.restart_required.join(", ")}.`}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
