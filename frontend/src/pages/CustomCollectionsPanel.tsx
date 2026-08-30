import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
// The overrides document and its helpers are shared with the settings page
// and the sibling panels -- see `api/overrides.ts` for why a second copy of
// the seeding rule would be a bug rather than a duplication.
import {
  documentFromConfig,
  fieldErrors,
  readPath,
  withPath,
  withoutPath,
} from "../api/overrides";
import type {
  ConfigPreviewResponse,
  ConfigResponse,
  ConfigSaveResponse,
  DefinitionsListingResponse,
  DefinitionSummary,
  OverridesDocument,
  ParsedSourceResponse,
} from "../api/types";
import "./custom-collections.css";

/** Where every write here lands. The overrides layer replaces a list
 * WHOLESALE, so this panel always writes the complete override list -- and
 * builds it only from `overrideList` below, never from the whole config and
 * never from the server's listing rows (the freezing hazard
 * `api/overrides.ts` opens with: the listing is a seven-field projection of
 * a definition that carries a dozen more, so an entry rebuilt from it would
 * drop `summary`, `limit`, `schedule` and the rest -- and an entry the
 * mounted file supplies would be pinned at today's YAML values against every
 * future edit of that file). */
const DEFINITIONS_PATH = "collections.definitions";

/** The id the guard's sentence carries, so the disabled Create button can
 * point a screen reader at the reason it is disabled. */
const GUARD_ID = "custom-file-guard";

/** Facts C2: what Read URL, Check and Create actually verify -- shape,
 * never existence. */
const SHAPE_ONLY_NOTE =
  "The URL is checked for shape only — whether the list exists is discovered " +
  "when the reconcile pass next runs it, and a source that fails leaves its " +
  "collection untouched rather than emptying it.";

/** Facts C3: the orphan story on remove, one honest sentence -- plus why
 * there is no edit control. */
const REMOVE_NOTE =
  "Removing a definition only stops the pass building it — the collection " +
  "already in Plex follows collections.delete_unconfigured: reported as an " +
  "orphan by default, deleted only when that setting says so. There is no " +
  "edit: change a definition by removing and re-creating it.";

/** The HARD GUARD (facts Addendum), and the whole of its explanation.
 *
 * Not a warning beside a working button: while the listing carries a
 * file-provenance row, creating here is refused. The two layers cannot
 * coexist -- an overrides list replaces the file's wholesale, and copying the
 * file's rows into the write is the freezing hazard -- so the honest answer
 * is to refuse and name the two modes that do work. Read URL stays live under
 * the guard on purpose: resolving a paste to its builder and params is
 * exactly what an operator managing definitions in YAML needs from this page. */
const FILE_ROWS_NOTE =
  "Creating here is refused while any definition below comes from the mounted " +
  "config file: the file's definitions do not merge with definitions created " +
  "here — the first save here would store an overrides list that replaces the " +
  "file's for as long as it exists, so every row marked config file would " +
  "quietly stop being built. Two ways forward: keep managing definitions in " +
  "the config file (Read URL below still resolves a pasted URL to the builder " +
  "and params to write there), or move those rows into this form once and " +
  "empty the file's definitions list.";

/** CatalogPanel's precedent, for a save from this panel. */
const APPLIES_NOTE =
  "The change is live in the running process, and the collection itself " +
  "appears at the next reconcile — use Diff now above to run one immediately.";

/** The stored override list, as seeded from the served config (the served
 * value IS the stored one -- an override wins the merge). Absent exactly
 * when the mounted file's list is in force, which is when nothing may be
 * copied in. */
function overrideList(stored: OverridesDocument): unknown[] {
  const value = readPath(stored, DEFINITIONS_PATH);
  return Array.isArray(value) ? value : [];
}

/** The stored document plus one created entry, list written whole. */
function documentForCreate(
  stored: OverridesDocument,
  entry: Record<string, unknown>,
): OverridesDocument {
  return withPath(stored, DEFINITIONS_PATH, [...overrideList(stored), entry]);
}

/** The stored document minus the override entry at `ordinal`.
 *
 * Removing the last entry drops the key instead of writing `[]`: the key
 * going away is the overrides contract's revert, and it hands the decision
 * back to whatever the mounted file lists. */
function documentForRemove(
  stored: OverridesDocument,
  ordinal: number,
): OverridesDocument {
  const remaining = overrideList(stored).filter((_, at) => at !== ordinal);
  return remaining.length === 0
    ? withoutPath(stored, DEFINITIONS_PATH)
    : withPath(stored, DEFINITIONS_PATH, remaining);
}

/** A listing row's position within the stored override list: its index among
 * the override-provenance rows. Provenance is uniform today (the wholesale
 * replace makes the supplying layer single-valued), so this equals the raw
 * index -- counting keeps the mapping right regardless. */
function overrideOrdinal(definitions: DefinitionSummary[], index: number): number {
  return (
    definitions.slice(0, index + 1).filter((row) => row.provenance === "override")
      .length - 1
  );
}

/** A parse refusal, in whichever of the endpoint's two 422 shapes it arrives.
 *
 * The handler's own refusal is a string `detail`, which `api/client.ts` hands
 * over as the error's message. A body FastAPI's request validator rejects is
 * a list of `{loc, msg}` entries instead, and the message is then the generic
 * "request failed with 422" -- so the sentences are dug out of the detail
 * rather than rendering an object. */
function refusalMessage(caught: unknown): string {
  if (caught instanceof ApiError && Array.isArray(caught.detail)) {
    const messages = Object.values(fieldErrors(caught.detail));
    if (messages.length > 0) return messages.join("; ");
  }
  return (caught as Error).message;
}

/** The config-defined collection definitions, and the form that creates one
 * from a pasted list URL.
 *
 * The config surface, deliberately apart from the DefinitionsPanel above it:
 * that panel runs a real, Plex-touching dry run over these same definitions,
 * while everything here is config reads and config writes -- so this panel
 * renders on a replica where the Plex-touching panels report 503, the same
 * posture as the catalog and groups panels.
 *
 * Two refusals shape it, and they are not the same refusal. Create is refused
 * outright while any listed row comes from the mounted file (the guard above,
 * facts Addendum). And whatever is written is built from the STORED overrides
 * document, never from the listing -- the listing supplies display, provenance
 * and the stored ordinal, nothing else.
 *
 * Remove exists only for override-provenance rows -- create's undo (facts
 * C3). A file row renders with a badge and no control.
 */
export function CustomCollectionsPanel() {
  const [listing, setListing] = useState<DefinitionsListingResponse | null>(null);
  // The overrides the server already holds. Kept whole rather than reduced
  // to the one path: a save here must not drop an override another page
  // stored.
  const [stored, setStored] = useState<OverridesDocument>({});
  const [loadError, setLoadError] = useState<string | null>(null);

  // The create form.
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [parsed, setParsed] = useState<ParsedSourceResponse | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});

  // One busy key for whichever request is in flight ("parsing", "checking",
  // "saving", or "removing-<n>"), so only one write races nothing.
  const [busy, setBusy] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Separate from `saveError` on purpose: a re-read that fails after the
  // store succeeded did not unsave anything, and reporting it as a save
  // failure would put a red error beside "Saved."
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [checked, setChecked] = useState<ConfigPreviewResponse | null>(null);
  const [result, setResult] = useState<ConfigSaveResponse | null>(null);

  // A ref rather than an effect-local `cancelled`: the save handler's
  // continuation lands outside any effect, the same reasoning the
  // neighbouring panels use.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const adopt = useCallback(
    (definitions: DefinitionsListingResponse, config: ConfigResponse) => {
      setListing(definitions);
      setStored(documentFromConfig(config));
      // Scope starts as "every library": all boxes checked, which the entry
      // builder writes as NO `libraries` key at all.
      setChosen(
        Object.fromEntries(definitions.libraries.map((name) => [name, true])),
      );
    },
    [],
  );

  const reload = useCallback(async () => {
    const [definitions, config] = await Promise.all([
      apiFetch<DefinitionsListingResponse>("/api/collections/definitions"),
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
        <h2>Custom collections</h2>
        <p className="page-error">{loadError}</p>
      </section>
    );
  }

  if (listing === null) {
    return (
      <section className="panel custom-collections-panel">
        <h2>Custom collections</h2>
        <p className="muted">Loading…</p>
      </section>
    );
  }

  const fileRows = listing.definitions.some((row) => row.provenance === "file");
  const chosenNames = listing.libraries.filter((name) => chosen[name]);
  const allChosen = chosenNames.length === listing.libraries.length;
  const complete = parsed !== null && title.trim() !== "" && chosenNames.length > 0;
  // Check as well as Create: Check asks whether the server would accept the
  // very document the guard refuses to send, so answering "checks out" under
  // the guard would be an offer this panel will not honour.
  const ready = complete && !fileRows;

  /** Any edit invalidates what a previous Check or save reported (the
   * CatalogPanel posture: a 422 pinned to a definition nobody is proposing
   * any more is a refusal of nothing). */
  function touch() {
    setResult(null);
    setChecked(null);
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
      // configured library" default (`None`). An explicit full list would
      // freeze today's library names into the definition.
      ...(allChosen ? {} : { libraries: chosenNames }),
    };
  }

  async function parse() {
    setBusy("parsing");
    setParsed(null);
    setParseError(null);
    touch();
    try {
      const response = await apiFetch<ParsedSourceResponse>(
        "/api/collections/parse-source",
        { method: "POST", body: JSON.stringify({ url }) },
      );
      if (live.current) setParsed(response);
    } catch (caught) {
      // The refusal -- the params model's own error string, or the trakt
      // fence -- lands here verbatim; see `refusalMessage` for the other
      // shape the same status can arrive in.
      if (live.current) setParseError(refusalMessage(caught));
    } finally {
      if (live.current) setBusy(null);
    }
  }

  async function check() {
    setBusy("checking");
    touch();
    try {
      const response = await apiFetch<ConfigPreviewResponse>(
        "/api/config/preview",
        {
          method: "POST",
          body: JSON.stringify({ document: documentForCreate(stored, entry()) }),
        },
      );
      if (live.current) setChecked(response);
    } catch (caught) {
      if (live.current) {
        if (caught instanceof ApiError && caught.status === 422) {
          setErrors(fieldErrors(caught.detail));
          setSaveError("The server rejected this definition.");
        } else {
          setSaveError((caught as Error).message);
        }
      }
    } finally {
      if (live.current) setBusy(null);
    }
  }

  /** One PUT for create and remove -- the sibling panels' idiom verbatim:
   * store, then re-read OUTSIDE the try, because a failed re-read after a
   * successful store is staleness, not an unsaved change. */
  async function put(document: OverridesDocument, key: string) {
    setBusy(key);
    touch();
    let saved = false;
    try {
      const response = await apiFetch<ConfigSaveResponse>("/api/config/overrides", {
        method: "PUT",
        body: JSON.stringify({ document }),
      });
      if (live.current) setResult(response);
      saved = true;
    } catch (caught) {
      if (live.current) {
        if (caught instanceof ApiError && caught.status === 422) {
          setErrors(fieldErrors(caught.detail));
          setSaveError("The server rejected this change.");
        } else {
          setSaveError((caught as Error).message);
        }
      }
    }
    if (saved) {
      if (live.current && key === "saving") {
        // The created definition is stored; a form still holding it would
        // invite a duplicate-title 422 on the very next click.
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
            `Saved, but the panel could not be re-read (${(caught as Error).message}). ` +
              "What is shown may be stale — reload the page.",
          );
        }
      }
    }
    if (live.current) setBusy(null);
  }

  return (
    <section className="panel custom-collections-panel">
      <h2>Custom collections</h2>

      <p className="muted custom-note">
        The config's own <span className="mono">collections.definitions</span> —
        what the pass builds beyond the built-ins and the catalog's presets.
        Create one from a list URL below; it is stored as a config override,
        like the settings page's edits.
      </p>
      <p className="muted custom-note">{REMOVE_NOTE}</p>
      {fileRows && (
        <p className="muted custom-note custom-file-note" id={GUARD_ID}>
          {FILE_ROWS_NOTE}
        </p>
      )}

      {listing.definitions.length === 0 ? (
        <p className="empty">No definitions are configured — create one below.</p>
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
                      {row.provenance === "file" ? "config file" : "override"}
                    </span>
                  </td>
                  <td>
                    {/* Remove is create's undo, so it exists only for rows
                        the overrides document supplies. A file row's missing
                        control IS the freezing guard made visible: this
                        panel never writes file entries anywhere. */}
                    {row.provenance === "override" && (
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
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h3 className="custom-form-title">Create from a list URL</h3>
      <p className="muted custom-note">{SHAPE_ONLY_NOTE}</p>

      <div className="custom-form">
        <label className="custom-field">
          <span>Title</span>
          <input
            type="text"
            value={title}
            aria-label="Collection title"
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
        {/* Live under the guard: an operator who must keep managing
            definitions in YAML still needs a paste turned into a builder and
            params, and reading a URL stores nothing. */}
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
        <p className="custom-parsed" role="status">
          Builder: <span className="mono">{parsed.builder}</span>
          {" — "}
          {parsed.display_note}
        </p>
      )}

      <fieldset className="custom-libraries">
        <legend className="muted">
          Libraries — all checked applies it to every configured library
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
          onClick={() => void check()}
        >
          {busy === "checking" ? "Checking…" : "Check"}
        </button>
        <button
          type="button"
          disabled={busy !== null || !ready}
          // A disabled control whose reason sits four paragraphs above it is
          // a dead end for a screen reader; the guard's own sentence is the
          // description.
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

      {checked !== null && (
        <p className="custom-checked" role="status">
          {`Checks out — the server would accept this definition (config ` +
            `${checked.version_before} → ${checked.version_after}). Nothing was stored.`}
        </p>
      )}

      {result !== null && (
        <div className="custom-saved" role="status">
          <p>{`Saved. Config ${result.version_before} → ${result.version_after}.`}</p>
          <p className="muted">{APPLIES_NOTE}</p>
          {reloadError !== null && <p className="custom-stale">{reloadError}</p>}
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
