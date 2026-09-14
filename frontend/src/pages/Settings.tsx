import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
// The overrides document's helpers live beside the API client now that the
// catalog picker writes the same document through the same endpoints -- see
// that module's header for why they are shared rather than copied.
import {
  documentFromConfig,
  fieldErrors,
  hasPath,
  isPlainObject,
  keepContract,
  PROVENANCE_KEYS,
  readPath,
  revisionFromConfig,
  saveBody,
  STALE_SAVE_NOTE,
  withPath,
  withoutPath,
} from "../api/overrides";
import type {
  ConfigApplyResponse,
  ConfigPreviewResponse,
  ConfigResponse,
  ConfigSaveResponse,
  OverridesDocument,
} from "../api/types";
import { ProviderAttribution } from "../ProviderAttribution";
import { ConfigSafetyPanel } from "./ConfigSafetyPanel";
import { LibraryOverridesPanel } from "./LibraryOverridesPanel";
import { WebhookSecretPanel } from "./WebhookSecretPanel";
import "./settings.css";

/** The attribution block and its required wording live with the component now
 * that the candidate picker carries the same block. Re-exported here because
 * this page's tests -- which are where the licence wording is pinned on the
 * component side -- name it as this module's. */
export { TMDB_NOTICE, TVDB_NOTICE } from "../ProviderAttribution";

/** The server's redaction marker (routes.py::get_config). It is a state, not
 * a value, so the renderer shows it as a badge rather than the raw string. */
const REDACTED_MARKER = "***REDACTED***";

/** What a field whose served value is redacted says about itself.
 *
 * The value in the box is the server's redacted rendering, not the stored
 * setting -- `notifications.url` arrives as a bare host with its push token
 * stripped. Leaving it alone keeps the stored value (the document carries the
 * server's keep sentinel for it); typing replaces the stored value outright.
 * The operator has to be told which of those they are about to do. */
export const REDACTED_EDIT_NOTE =
  "The stored value is hidden and kept as it is. Typing here replaces it.";

/** Why the preview's number is "~N" and not "N", quoted from the module that
 * computes it (`src/autoposter/config/impact.py`, its docstring) rather than
 * paraphrased. The direction of the error is the load-bearing part: an
 * operator who reads the count as exact will read a full-library number as a
 * catastrophe, and one who reads it as merely "roughly" will not know which
 * way to discount it. */
export const IMPACT_CAVEAT =
  "The consequence is honest and one-directional: a poster whose real render " +
  "composited a logo will not match the recomputed value, so it is reported " +
  "as affected whatever the edit was. That is an overcount, never an " +
  "undercount, of the text and version changes the operator is actually " +
  "asking about.";

/** What the breakdown means.
 *
 * The render version is computed per art kind (`config/loader.py`'s
 * `render_version_for`), so a kind counted below is one whose stored
 * fingerprint the edit moved -- not, without qualification, one the edit
 * touched: a poster with a composited logo is counted for any
 * render-affecting edit, because the walk cannot see the logo it was built
 * with (see `IMPACT_CAVEAT`). That distinction aside, this is still the
 * exact inverse of what this note said while one wholesale hash covered the
 * whole artwork section, and it is the user-visible half of roadmap row 111.
 * A shared input -- an asset root, `use_original_title`, `output_quality` --
 * is a member of every kind's payload and still reaches all four; that is
 * the edit being global rather than the breakdown failing to discriminate,
 * and it is why the note holds in both arms below. The other half -- that a
 * kind missing from the breakdown was excluded by a gate -- is unchanged. */
const IMPACT_BREAKDOWN_PER_KIND_NOTE =
  "The render version is per art kind, so a kind counted below is one " +
  "whose stored fingerprint this edit moves; a poster with a composited " +
  "logo is counted for any render-affecting edit, because the walk cannot " +
  "see the logo it was built with.";
const IMPACT_BREAKDOWN_GATE_NOTE =
  "A kind missing from the breakdown was excluded by a gate — disabled, or " +
  "skipped by rule.";

/** snake_case -> "Snake case". Derived, never looked up: the config schema
 * grows every phase, and a label table would drift. */
function labelFor(key: string): string {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The reason a restart is needed for `path`, or undefined if it is live.
 * `frozen_paths` keys are prefixes: `notifications` freezes everything under
 * it. `live` wins over all of them -- a path the server reads per use is live
 * however broad the prefix above it is, the same live-over-frozen rule
 * `config/live.py`'s `frozen_reason` applies server-side. Unlike that
 * function, this loop does not also break ties between overlapping frozen
 * prefixes by length -- no two entries in `frozen_paths` overlap today, so
 * the two cannot yet disagree. */
function frozenReason(
  frozen: Record<string, string>,
  live: string[],
  path: string,
): string | undefined {
  if (live.some((prefix) => path === prefix || path.startsWith(`${prefix}.`))) {
    return undefined;
  }
  for (const [prefix, reason] of Object.entries(frozen)) {
    if (path === prefix || path.startsWith(`${prefix}.`)) return reason;
  }
  return undefined;
}

/** Everything a row needs to be editable. `null` in a row's place of this is
 * how the secrets panel stays read-only. */
export interface Editor {
  document: OverridesDocument;
  /** The document as the server last served it. The pending one holds the
   * whole configuration, so "is this row carried by the document" no longer
   * distinguishes anything -- every row is. What a row still needs to know is
   * whether IT was changed, which is these two disagreeing at its path. */
  saved: OverridesDocument;
  frozen: Record<string, string>;
  /** Paths this service derives rather than the operator setting, and paths a
   * frozen prefix covers but which are read per use. Both come from the
   * server: the editor must not carry a second copy of either judgement. */
  computed: string[];
  live: string[];
  /** Paths whose served value is the server's redacted rendering rather than
   * the stored setting, and the marker that means "keep what is stored". */
  redacted: string[];
  sentinel: string;
  errors: Record<string, string>;
  setValue: (path: string, value: unknown) => void;
  /** Take one row out of the document, so whatever would supply it in the
   * document's absence does -- the library matrix's "inherit". */
  clear: (path: string) => void;
  /** Put one row back to the value the server served for it, which is what an
   * emptied box means: the operator wiped a field mid-edit, not asked for the
   * setting to go away. */
  revert: (path: string) => void;
}

function ScalarValue({ value }: { value: unknown }) {
  if (value === REDACTED_MARKER) {
    return <span className="config-pill redacted">redacted</span>;
  }
  if (typeof value === "boolean") {
    return (
      <span className={`config-pill ${value ? "on" : "off"}`}>
        {value ? "on" : "off"}
      </span>
    );
  }
  if (value === "" || value === null) {
    return <span className="muted">(not set)</span>;
  }
  return <>{String(value)}</>;
}

function ListValue({ value }: { value: unknown[] }) {
  if (value.length === 0) {
    return <span className="muted">(none)</span>;
  }
  // A list of scalars reads best as a single joined value; a list holding
  // objects gets one row per entry instead.
  if (value.every((item) => !isPlainObject(item) && !Array.isArray(item))) {
    return <>{value.map(String).join(", ")}</>;
  }
  return (
    <ol className="config-list">
      {value.map((item, index) => (
        <li key={index}>
          {isPlainObject(item) ? (
            <ConfigNode value={item} />
          ) : Array.isArray(item) ? (
            <ListValue value={item} />
          ) : (
            <ScalarValue value={item} />
          )}
        </li>
      ))}
    </ol>
  );
}

/** A string list, edited as a list rather than as a comma-joined string: the
 * document carries the whole list at its own path, because that is the unit
 * the API merges. */
function StringListField({
  path,
  value,
  onChange,
}: {
  path: string;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <span className="config-list-edit">
      {value.map((item, index) => (
        <span className="config-list-item" key={index}>
          <input
            type="text"
            aria-label={`${path}[${index}]`}
            value={item}
            onChange={(event) =>
              onChange(value.map((v, i) => (i === index ? event.target.value : v)))
            }
          />
          <button
            type="button"
            aria-label={`Remove ${path}[${index}]`}
            onClick={() => onChange(value.filter((_, i) => i !== index))}
          >
            Remove
          </button>
        </span>
      ))}
      <button
        type="button"
        aria-label={`Add to ${path}`}
        onClick={() => onChange([...value, ""])}
      >
        Add
      </button>
    </span>
  );
}

/** The widget is chosen from the value the *server* serves, not from what is
 * currently typed: picking off the pending value would swap a number input
 * for a text one the moment the field was cleared, losing focus mid-edit.
 * A type with no editor -- null, a list of objects, a redaction -- keeps the
 * read-only rendering, which is also how a schema this page has never seen
 * stays safe. */
function Field({
  path,
  base,
  current,
  editor,
  title,
}: {
  path: string;
  base: unknown;
  current: unknown;
  editor: Editor | null;
  title?: string;
}) {
  const readOnly = (
    <>
      {Array.isArray(current) ? (
        <ListValue value={current} />
      ) : (
        <ScalarValue value={current} />
      )}
    </>
  );
  if (editor === null || base === REDACTED_MARKER) return readOnly;

  if (typeof base === "boolean") {
    return (
      <input
        type="checkbox"
        aria-label={path}
        title={title}
        checked={current === true}
        onChange={(event) => editor.setValue(path, event.target.checked)}
      />
    );
  }
  if (typeof base === "number") {
    return (
      <input
        type="number"
        aria-label={path}
        title={title}
        value={typeof current === "number" ? String(current) : ""}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") editor.revert(path);
          else editor.setValue(path, Number(raw));
        }}
      />
    );
  }
  if (typeof base === "string") {
    return (
      <input
        type="text"
        aria-label={path}
        title={title}
        value={typeof current === "string" ? current : ""}
        onChange={(event) => editor.setValue(path, event.target.value)}
      />
    );
  }
  if (Array.isArray(base) && base.every((item) => typeof item === "string")) {
    return (
      <StringListField
        path={path}
        value={(Array.isArray(current) ? current : []).map(String)}
        onChange={(next) => editor.setValue(path, next)}
      />
    );
  }
  return readOnly;
}

function ConfigRow({
  name,
  path,
  value,
  editor,
  description,
}: {
  name: string;
  path: string;
  value: unknown;
  editor: Editor | null;
  description?: string;
}) {
  // A computed path is the server's to set: an override on it is recomputed
  // away, so offering an input would be offering an edit that does nothing.
  // Handled by dropping the editor for this row alone, which is the same
  // mechanism the secrets panel already uses.
  const rowEditor =
    editor !== null && editor.computed.includes(path) ? null : editor;
  const edited =
    rowEditor !== null &&
    readPath(rowEditor.document, path) !== readPath(rowEditor.saved, path);
  const pending =
    edited && rowEditor !== null ? readPath(rowEditor.document, path) : value;
  const isRedacted = rowEditor !== null && rowEditor.redacted.includes(path);
  // The sentinel is a state, not a value: it says "the stored setting stays as
  // it is", so the field shows the server's redacted rendering of that setting
  // rather than the marker. Typing replaces the marker with what was typed,
  // and from then on the typed value is what shows.
  const current = isRedacted && pending === rowEditor.sentinel ? value : pending;
  const restart =
    edited && rowEditor !== null
      ? frozenReason(rowEditor.frozen, rowEditor.live, path)
      : undefined;
  const error = rowEditor?.errors[path];

  return (
    <div className="config-row">
      {/* The description is the row's whole documentation, and it hangs off
          the label as a native hover -- the page's existing idiom for
          secondary text (the redaction note, the restart reason, the impact
          caveat) rather than a new component. An absent or empty description
          passes `undefined`, because an empty `title` is a hover that opens
          onto nothing. */}
      <span className="config-key" title={description || undefined}>
        {labelFor(name)}
      </span>
      <span className="config-value">
        <Field
          path={path}
          base={value}
          current={current}
          editor={rowEditor}
          title={isRedacted ? REDACTED_EDIT_NOTE : undefined}
        />
        {restart !== undefined && (
          <span className="config-pill restart" title={restart}>
            restart to apply
          </span>
        )}
        {error !== undefined && (
          <span className="config-field-error" role="alert">
            {error}
          </span>
        )}
      </span>
    </div>
  );
}

/** Recursive renderer driven entirely by the response's shape: scalars and
 * lists become label/value rows, nested objects become indented subsections.
 * Nothing here names a config field, so a new key appears without a frontend
 * change. `path` accumulates the dotted path the API speaks in, and
 * `descriptions` is keyed on exactly that path. */
function ConfigNode({
  value,
  path = "",
  editor = null,
  descriptions = {},
}: {
  value: Record<string, unknown>;
  path?: string;
  editor?: Editor | null;
  descriptions?: Record<string, string>;
}) {
  return (
    <div className="config-node">
      {Object.entries(value).map(([key, entry]) => {
        const childPath = path === "" ? key : `${path}.${key}`;
        return isPlainObject(entry) ? (
          <div className="config-subsection" key={key}>
            <h3>{labelFor(key)}</h3>
            <ConfigNode
              value={entry}
              path={childPath}
              editor={editor}
              descriptions={descriptions}
            />
          </div>
        ) : (
          <ConfigRow
            key={key}
            name={key}
            path={childPath}
            value={entry}
            editor={editor}
            description={descriptions[childPath]}
          />
        );
      })}
    </div>
  );
}

/** One titled panel per top-level object, in the server's own key order --
 * which is the config model's declaration order, the same order the example
 * YAML documents. Two documented exceptions to pure shape-driven rendering:
 * top-level scalars (assets_root, workers, ...) have no section of their own,
 * so they are gathered into a leading "General" panel; and `secrets` is
 * pinned last -- the server already appends it last, but an all-redacted
 * panel drifting into the middle of the page on a server refactor would be a
 * regression worth defending against here. */
function ConfigSections({
  config,
  editor,
}: {
  config: ConfigResponse;
  editor: Editor;
}) {
  // Descriptions reach every row, including the secrets panel's -- those
  // render `***REDACTED***` and nothing else, so the description is the only
  // thing on the row that says anything. That is why this is a prop of its own
  // rather than a field of `Editor`, which the secrets panel deliberately does
  // not get.
  const descriptions = isPlainObject(config.field_descriptions)
    ? Object.fromEntries(
        Object.entries(config.field_descriptions).map(([key, text]) => [
          key,
          String(text),
        ]),
      )
    : {};
  // `secrets` is on that list and is the one key this page still renders: the
  // panel below shows it read-only, and what the list means is "never send
  // this back", which is the document's rule rather than the page's.
  const entries = Object.entries(config).filter(
    ([key]) => key === "secrets" || !PROVENANCE_KEYS.includes(key),
  );
  const general = entries.filter(([, value]) => !isPlainObject(value));
  const sections = entries
    .filter((entry): entry is [string, Record<string, unknown>] =>
      isPlainObject(entry[1]),
    )
    // Array.prototype.sort is stable, so everything but `secrets` keeps the
    // server's order.
    .sort(([a], [b]) => Number(a === "secrets") - Number(b === "secrets"));

  return (
    <>
      {general.length > 0 && (
        <section className="panel config-section">
          <h2>General</h2>
          <ConfigNode
            value={Object.fromEntries(general)}
            editor={editor}
            descriptions={descriptions}
          />
        </section>
      )}
      {sections.map(([key, value]) => (
        <section className="panel config-section" key={key}>
          <h2>{labelFor(key)}</h2>
          {/* Secrets are environment-only and the API refuses a document
              carrying one at any depth, so the panel gets no editor at all
              rather than disabled inputs. */}
          <ConfigNode
            value={value}
            path={key}
            editor={key === "secrets" ? null : editor}
            descriptions={descriptions}
          />
        </section>
      ))}
    </>
  );
}

/** What the preview said this document would cost.
 *
 * `impact: null` is not "zero items": it is the server saying the edit cannot
 * change a rendered image at all, so the walk was never run (routes.py's
 * `_render_affecting`). Rendering it as a count of zero would invite the
 * operator to compare it against a real one.
 *
 * Collection posters are counted separately and are NOT part of `impact`:
 * `config/impact.py` walks the `renders` table, which has no row for a
 * collection, so a `collections.poster_title` edit shows "no re-renders"
 * beside a real collection-poster cost. Both sentences are true at once. */
function ImpactReport({
  impact,
  collectionPosters,
}: {
  impact: ConfigPreviewResponse["impact"];
  collectionPosters: number;
}) {
  const posters =
    collectionPosters > 0 ? (
      <p className="muted config-impact-note">
        {`~${collectionPosters} managed collection posters would be re-composited and re-uploaded, once.`}
      </p>
    ) : null;

  if (impact === null) {
    return (
      <>
        <p className="config-impact none">
          No re-renders — this change does not affect rendered artwork.
        </p>
        {posters}
      </>
    );
  }

  // Every examined row affected no longer means "any artwork edit looks like
  // this" -- since row 111 it means the edit really did reach every kind,
  // which is what a shared input (an asset root, use_original_title,
  // output_quality) does. So the sentence says that rather than blaming the
  // artwork section.
  const everyExaminedRow = impact.of_total > 0 && impact.affected === impact.of_total;
  const renders = `~${impact.affected} of ${impact.of_total} artwork renders`;
  const kinds = Object.entries(impact.by_art_kind);

  return (
    <div className="config-impact">
      <p className="config-impact-count" title={IMPACT_CAVEAT}>
        {everyExaminedRow
          ? `This edit reaches every examined row — ${renders}.`
          : `${renders} are out of date.`}
      </p>
      {kinds.length > 0 && (
        <>
          <ul className="config-impact-kinds">
            {kinds.map(([kind, affected]) => (
              <li key={kind}>{`${kind}: ${affected}`}</li>
            ))}
          </ul>
          <p className="muted config-impact-note">
            {`${IMPACT_BREAKDOWN_PER_KIND_NOTE} ${IMPACT_BREAKDOWN_GATE_NOTE}`}
          </p>
        </>
      )}
      {posters}
    </div>
  );
}

/** Which action is in flight, if any. One value rather than three booleans:
 * the three are mutually exclusive and every button is disabled for all of
 * them, so two of three booleans would only ever be a way to disagree. */
type Action = "preview" | "save" | "apply";

export function Settings() {
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // `saved` is the document as the server holds it; `document` is that plus
  // whatever is pending. Dirtiness is the difference between the two, so
  // editing a field back to its stored value stops being a change.
  const [savedDocument, setSavedDocument] = useState<OverridesDocument>({});
  const [pendingDocument, setPendingDocument] = useState<OverridesDocument>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saveError, setSaveError] = useState<string | null>(null);
  const [result, setResult] = useState<
    ConfigSaveResponse | ConfigApplyResponse | null
  >(null);
  const [preview, setPreview] = useState<ConfigPreviewResponse | null>(null);
  const [busy, setBusy] = useState<Action | null>(null);
  // The revision of the document `savedDocument` was seeded from. Sent with
  // every write so the server can refuse one composed against a document that
  // has since moved -- the whole of clause 8.
  const [storedRevision, setStoredRevision] = useState<string | null>(null);
  // Deliberately NOT `saveError`: that one lives inside the pending panel,
  // and a stale save is followed by a re-seed that makes the pending panel
  // disappear. The operator would be told nothing at all.
  const [staleNote, setStaleNote] = useState<string | null>(null);

  const adopt = useCallback((response: ConfigResponse) => {
    const stored = documentFromConfig(response);
    setConfig(response);
    setSavedDocument(stored);
    setPendingDocument(stored);
    setStoredRevision(revisionFromConfig(response));
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiFetch<ConfigResponse>("/api/config")
      .then((response) => {
        if (!cancelled) adopt(response);
      })
      .catch((caught: Error) => {
        if (!cancelled) setError(caught.message);
      });
    return () => {
      cancelled = true;
    };
  }, [adopt]);

  const keep = config === null ? { paths: [], sentinel: "" } : keepContract(config);
  const editor: Editor = {
    document: pendingDocument,
    saved: savedDocument,
    redacted: keep.paths,
    sentinel: keep.sentinel,
    frozen: isPlainObject(config?.frozen_paths)
      ? Object.fromEntries(
          Object.entries(config.frozen_paths).map(([key, reason]) => [
            key,
            String(reason),
          ]),
        )
      : {},
    computed: Array.isArray(config?.computed_paths)
      ? config.computed_paths.filter(
          (path): path is string => typeof path === "string",
        )
      : [],
    live: Array.isArray(config?.live_paths)
      ? config.live_paths.filter(
          (path): path is string => typeof path === "string",
        )
      : [],
    errors,
    // A preview answers a question about one exact document, so any further
    // edit retires it. Showing a stale count next to a changed document is
    // worse than showing none.
    setValue: (path, value) => {
      setPreview(null);
      setPendingDocument((current) => withPath(current, path, value));
    },
    // Clearing removes the key. It never writes null -- see the document
    // helpers in api/overrides.ts.
    clear: (path) => {
      setPreview(null);
      setPendingDocument((current) => withoutPath(current, path));
    },
    // Reverting puts the served value back, and drops the key outright when
    // the response carried none -- which is the only thing it could mean
    // there. Not the same operation as clearing: the document holds the whole
    // configuration, so dropping a key an emptied box was showing would ask
    // for the schema's default rather than undo a half-finished edit.
    revert: (path) => {
      setPreview(null);
      const served = readPath(savedDocument, path);
      setPendingDocument((current) =>
        served === undefined
          ? withoutPath(current, path)
          : withPath(current, path, served),
      );
    },
  };

  const pending = JSON.stringify(pendingDocument, null, 2);
  const dirty = pending !== JSON.stringify(savedDocument, null, 2);

  /** The three actions, which differ only in the request they send.
   *
   * They share one body -- `pendingDocument`, the same object the Save arm
   * has always sent -- so the keep sentinel travels with a preview and an
   * apply exactly as it does with a save. Rebuilding it per action is how the
   * three would drift apart, and the one that drifted would be the one that
   * destroyed a push token.
   *
   * They also share the error handling: all three answer with the same 422
   * body, because the server validates all three through one function.
   */
  async function submit(action: Action) {
    setBusy(action);
    setErrors({});
    setSaveError(null);
    setStaleNote(null);
    // A fresh preview answers a question about the document as it stands now;
    // a "Saved..." panel from an earlier commit sitting beside it would read
    // as though that save already accounted for what the preview is about to
    // show.
    setResult(null);
    const body = saveBody(pendingDocument, storedRevision);
    try {
      if (action === "preview") {
        setPreview(
          await apiFetch<ConfigPreviewResponse>("/api/config/preview", {
            method: "POST",
            body,
          }),
        );
        return;
      }
      const response =
        action === "apply"
          ? await apiFetch<ConfigApplyResponse>("/api/config/apply", {
              method: "POST",
              body,
            })
          : await apiFetch<ConfigSaveResponse>("/api/config/overrides", {
              method: "PUT",
              body,
            });
      setResult(response);
      // The document is stored now, so the count that described storing it has
      // nothing left to say.
      setPreview(null);
      // Provenance is the server's to report: which paths are overridden now
      // is a fact about what it stored, not about what was typed here.
      adopt(await apiFetch<ConfigResponse>("/api/config"));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        // Never a retry. Re-seed from the server and hand the operator back a
        // page that tells the truth, with their edit discarded and said so.
        setStaleNote(STALE_SAVE_NOTE);
        try {
          adopt(await apiFetch<ConfigResponse>("/api/config"));
        } catch (reread) {
          setSaveError((reread as Error).message);
        }
      } else if (caught instanceof ApiError && caught.status === 422) {
        setErrors(fieldErrors(caught.detail));
        setSaveError("The server rejected these settings.");
      } else {
        setSaveError((caught as Error).message);
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      {error !== null && <p className="page-error">{error}</p>}

      <section className="panel attribution">
        <ProviderAttribution />
      </section>

      <section className="panel">
        <h2>Running configuration</h2>
        <p className="muted config-note">
          Edit a field and save to store it as an override. Overrides are kept
          by this service and merged over the deployed configuration file;
          clearing one hands the setting back to that file. Secrets are
          redacted by the server, never sent to this page, and never editable
          here.
        </p>
        {config === null && <p className="muted">Loading…</p>}
        {staleNote !== null && <p className="page-error">{staleNote}</p>}
        {result !== null && (
          <>
            <p className="config-saved">
              {`Saved. Render version ${result.version_before} → ${result.version_after}.`}
            </p>
            {/* Only an apply reports a queue, and it reports both halves: the
                skipped items are ones the pending-dedupe arbiter found a job
                already waiting for, not ones that failed. */}
            {"queued" in result && (
              <p className="config-queued">
                {`Queued ${result.queued} items to re-render, ${result.skipped} already queued.`}
              </p>
            )}
            {result.restart_required.length > 0 && (
              <p className="config-restart">
                {`Restart required to apply: ${result.restart_required.join(", ")}`}
              </p>
            )}
            {/* Distinct from restart_required on purpose: these paths are
                frozen into the running application object itself, so not even
                a restart reaches them -- only editing the mounted config file
                does. Folding them into "restart required" would promise an
                operator a fix that restarting can never deliver. */}
            {(result.inert ?? []).length > 0 && (
              <p className="config-inert">
                {`Has no effect until it changes in the deployed config file: ${(result.inert ?? []).join(", ")}`}
              </p>
            )}
          </>
        )}
      </section>

      {config !== null && (
        <ConfigSafetyPanel
          revision={storedRevision}
          onChanged={async () => {
            adopt(await apiFetch<ConfigResponse>("/api/config"));
          }}
        />
      )}

      <WebhookSecretPanel />

      {dirty && (
        <section className="panel config-pending">
          <h2>Pending changes</h2>
          {/* The document itself, because it is what will be stored and what
              every error below is reported against. */}
          <pre className="config-document">{pending}</pre>
          {saveError !== null && <p className="page-error">{saveError}</p>}
          {/* An error naming a path that is not in the document has no field
              to sit at -- a malformed body reports at the body itself. It is
              still a reason the save failed, so it is shown rather than
              dropped. */}
          {Object.entries(errors)
            .filter(([path]) => !hasPath(pendingDocument, path))
            .map(([path, message]) => (
              <p className="page-error" key={path}>
                {path === "" ? message : `${path}: ${message}`}
              </p>
            ))}
          {preview !== null && (
            <>
              <ImpactReport
                impact={preview.impact}
                collectionPosters={preview.collection_posters ?? 0}
              />
              {preview.restart_required.length > 0 && (
                <p className="config-restart">
                  {`Restart required to apply: ${preview.restart_required.join(", ")}`}
                </p>
              )}
              {(preview.inert ?? []).length > 0 && (
                <p className="config-inert">
                  {`Has no effect until it changes in the deployed config file: ${(preview.inert ?? []).join(", ")}`}
                </p>
              )}
            </>
          )}
          {/* The two commits differ in what happens to the artwork, not in
              what gets stored, and that is the whole of the choice being
              offered here. */}
          <p className="muted config-actions-note">
            Save only stores the change and leaves the artwork alone — the
            drift sweep and the full pass pick the new fingerprints up in their
            own time. Apply now stores it and queues the affected items
            straight away.
          </p>
          <div className="config-actions">
            <button
              type="button"
              onClick={() => void submit("preview")}
              disabled={busy !== null}
            >
              Preview
            </button>
            <button
              type="button"
              onClick={() => void submit("save")}
              disabled={busy !== null}
            >
              Save only
            </button>
            <button
              type="button"
              onClick={() => void submit("apply")}
              disabled={busy !== null}
            >
              Apply now
            </button>
          </div>
        </section>
      )}

      {config !== null && (
        <LibraryOverridesPanel config={config} editor={editor} />
      )}
      {config !== null && <ConfigSections config={config} editor={editor} />}
    </>
  );
}
