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
import { DriftNotice } from "./DriftNotice";
import { LibraryOverridesPanel } from "./LibraryOverridesPanel";
import { RestartBanner } from "./RestartBanner";
import { SecretsPanel } from "./SecretsPanel";
import { ServersTab } from "./ServersTab";
import { SettingsAccordion } from "./SettingsAccordion";
import {
  GENERAL_TAB,
  TABS,
  tabForSection,
  useOpenSection,
  type TabId,
} from "./settingsTabs";
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
   * whether IT was changed, which is these two disagreeing at its path.
   *
   * That per-path comparison is `!==`, so for a list- or object-valued row it
   * is reference equality, and it is only correct because of an invariant
   * `adopt` holds: it seeds this and the pending document from the SAME
   * object, and `withPath` rebuilds only the spine down to the path it
   * changes, leaving every sibling reference shared. An `adopt` that built
   * the two documents separately -- two parses of the same response, say --
   * would make every non-scalar row read as edited at mount. */
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

/** The served descriptions, one string per dotted path.
 *
 * Read here rather than inside the tree because the secrets accordion wants
 * the same map for its own rows, and it is not part of the tree: the two
 * would otherwise normalise the same field twice and be free to disagree
 * about what a missing description is. */
function describedFields(config: ConfigResponse): Record<string, string> {
  return isPlainObject(config.field_descriptions)
    ? Object.fromEntries(
        Object.entries(config.field_descriptions).map(([key, text]) => [
          key,
          String(text),
        ]),
      )
    : {};
}

/** The keys the two accordions that are not config sections are remembered
 * under. Not section names: no config section can be called either of these,
 * so neither can ever collide with one. Both go through the page's
 * `useOpenSection`, which is what keeps one section open at a time and
 * remembers which -- the secrets accordion held its own `useState` before, so
 * it opened alongside whatever was already open and was forgotten on reload. */
const GENERAL_SECTION = "__general__";
const SECRETS_SECTION = "__secrets__";

/** The one tab panel's id, which every tab's `aria-controls` names. Fixed
 * rather than generated because there is only ever one Settings page, the
 * same reason `Sidebar.tsx` can hardcode `aria-controls="sidebar-nav"`. */
const TAB_PANEL_ID = "settings-tab-panel";

/** The sections of ONE tab, each in its own accordion.
 *
 * Shape-driven still: nothing here names a config field, and a section the
 * server grows appears without a frontend change -- on System, which is what
 * `tabForSection`'s fallback is for.
 *
 * Two documented departures from pure shape. The top-level scalars have no
 * section of their own, so they are gathered into a "General" accordion on
 * System; and `secrets` is not rendered from the config at all any more,
 * because an all-redacted read-only block is a weaker answer than the panel
 * that can actually set one.
 *
 * `libraries` is NOT a third omission: it is rendered here like every other
 * section, on the Libraries tab under the per-library matrix. The matrix is
 * the only thing that can say a cell is unset and follows the global, but it
 * is also the only thing that cannot edit one -- a per-library list or
 * mapping is a cell the matrix reports and this tree edits, and dropping the
 * tree would take that edit away with it. */
function ConfigSections({
  config,
  editor,
  tab,
  openSection,
  onOpen,
}: {
  config: ConfigResponse;
  editor: Editor;
  tab: TabId;
  openSection: string | null;
  onOpen: (section: string | null) => void;
}) {
  // Descriptions reach every row, and a row with no editable widget -- a
  // computed path, a shape with no editor -- has nothing else on it that says
  // anything. That is why this is a prop of its own rather than a field of
  // `Editor`, which such a row deliberately does not get.
  const descriptions = describedFields(config);
  const entries = Object.entries(config).filter(
    ([key]) => !PROVENANCE_KEYS.includes(key),
  );
  const general = entries.filter(([, value]) => !isPlainObject(value));
  const sections = entries.filter(
    (entry): entry is [string, Record<string, unknown>] =>
      isPlainObject(entry[1]) && tabForSection(entry[0]) === tab,
  );

  return (
    <>
      {/* No restart pill on General. `frozen_paths` keys are real paths, and
          this accordion is a bag of unrelated top-level scalars -- one reason
          pinned to its header would be wrong for every row it did not come
          from. The rows inside still carry their own pills when edited. */}
      {tab === GENERAL_TAB && general.length > 0 && (
        <SettingsAccordion
          title="General"
          open={openSection === GENERAL_SECTION}
          onToggle={() =>
            onOpen(openSection === GENERAL_SECTION ? null : GENERAL_SECTION)
          }
        >
          <ConfigNode
            value={Object.fromEntries(general)}
            editor={editor}
            descriptions={descriptions}
          />
        </SettingsAccordion>
      )}
      {sections.map(([key, value]) => (
        <SettingsAccordion
          key={key}
          title={labelFor(key)}
          open={openSection === key}
          onToggle={() => onOpen(openSection === key ? null : key)}
          restartReason={frozenReason(editor.frozen, editor.live, key)}
        >
          <ConfigNode
            value={value}
            path={key}
            editor={editor}
            descriptions={descriptions}
          />
        </SettingsAccordion>
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

/** One changed path: what the server holds for it, and what is pending. */
interface PendingChange {
  path: string;
  before: unknown;
  after: unknown;
}

/** The paths this edit changes, in the served key order.
 *
 * The pending document is the WHOLE configuration, so the panel that used to
 * render it as a JSON dump was showing the operator every setting the service
 * has in order to tell them about one. What they need before committing is
 * the difference, and only the difference. */
function changedPaths(
  pending: OverridesDocument,
  saved: OverridesDocument,
): PendingChange[] {
  const rows: PendingChange[] = [];
  const walk = (after: unknown, before: unknown, path: string) => {
    if (isPlainObject(after) && isPlainObject(before)) {
      for (const key of new Set([
        ...Object.keys(after),
        ...Object.keys(before),
      ])) {
        walk(after[key], before[key], path === "" ? key : `${path}.${key}`);
      }
      return;
    }
    // Structural comparison, so a list rebuilt element by element is unchanged
    // if it ends up the same list. A whole section that appeared or vanished
    // reports as one row rather than a row per leaf -- it is one decision.
    if (JSON.stringify(after) === JSON.stringify(before)) return;
    rows.push({ path, before, after });
  };
  walk(pending, saved, "");
  return rows;
}

/** A value as one side of a diff line.
 *
 * The keep sentinel reads as "(unchanged)", which is what it means: it is a
 * state the document carries at a redacted path, not a setting anyone ever
 * stored, and rendering it as itself reads as though the setting used to be
 * that string. Substituting the served rendering is not the alternative --
 * that would put a push token's bare host on screen as the value about to be
 * replaced.
 *
 * An absent key and a null read as "(not set)", the same words `ScalarValue`
 * uses for both -- one vocabulary for one state on one page. An empty string
 * keeps its own word: a diff is exactly where emptying a box has to be
 * distinguishable from taking the setting away, because those are two
 * different controls with two different outcomes. */
function diffValue(value: unknown, sentinel: string): string {
  if (sentinel !== "" && value === sentinel) return "(unchanged)";
  if (value === undefined || value === null) return "(not set)";
  if (typeof value === "boolean") return value ? "on" : "off";
  if (value === "") return "(empty)";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function PendingDiff({
  changes,
  sentinel,
}: {
  changes: PendingChange[];
  /** The server's keep marker for this response, or `""` when it sent none. */
  sentinel: string;
}) {
  return (
    <ul className="config-diff">
      {changes.map((change) => (
        <li key={change.path}>
          <code className="config-diff-path">{change.path}</code>
          <span className="config-diff-values">
            {`${diffValue(change.before, sentinel)} → ${diffValue(change.after, sentinel)}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Which tabs hold something unsaved. What the sticky bar names, so an
 * operator who edited two tabs and forgot one is told which.
 *
 * Read off `changedPaths` rather than comparing the documents again: a second
 * rule for "what counts as changed" is a second rule that can disagree with
 * the panel standing right above the bar, and the one here would have been
 * the key-order-sensitive one. Emitted in `TABS` order, which is the order
 * they sit in on screen. */
function tabsWithPendingEdits(
  pending: OverridesDocument,
  saved: OverridesDocument,
): TabId[] {
  const changed = new Set<TabId>(
    changedPaths(pending, saved).map((change) =>
      tabForSection(change.path.split(".")[0]),
    ),
  );
  return TABS.map((entry) => entry.id).filter((id) => changed.has(id));
}

/** Which action is in flight, if any. One value rather than three booleans:
 * the three are mutually exclusive and every button is disabled for all of
 * them, so two of three booleans would only ever be a way to disagree. */
type Action = "preview" | "save" | "apply";

/** The sticky bar: what is unsaved, where, and the four things to do with it.
 *
 * It names the tabs rather than the paths because the paths are already on
 * screen, in the panel above it; what the bar adds is the edit the operator
 * left behind on a tab they are no longer looking at. */
function PendingBar({
  tabsWithEdits,
  busy,
  onDiscard,
  onPreview,
  onSave,
  onApply,
}: {
  tabsWithEdits: TabId[];
  busy: Action | null;
  onDiscard: () => void;
  onPreview: () => void;
  onSave: () => void;
  onApply: () => void;
}) {
  const names = tabsWithEdits
    .map((id) => TABS.find((entry) => entry.id === id)?.label ?? id)
    .join(", ");
  return (
    <div
      className="settings-pending-bar"
      role="region"
      aria-label="Unsaved changes"
    >
      <span className="settings-pending-bar-note">
        {`Unsaved changes on: ${names}`}
      </span>
      <div className="settings-pending-bar-actions">
        <button type="button" onClick={onDiscard} disabled={busy !== null}>
          Discard
        </button>
        <button type="button" onClick={onPreview} disabled={busy !== null}>
          Preview impact
        </button>
        <button type="button" onClick={onSave} disabled={busy !== null}>
          Save
        </button>
        <button type="button" onClick={onApply} disabled={busy !== null}>
          Save and re-render
        </button>
      </div>
    </div>
  );
}

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
  // Which tab is showing, and which of its sections is unfolded. The open
  // section is remembered per browser and per tab, so moving between tabs
  // does not unfold the whole page.
  const [tab, setTab] = useState<TabId>("servers");
  const [openSection, setOpenSection] = useOpenSection(tab);

  const adopt = useCallback((response: ConfigResponse) => {
    const stored = documentFromConfig(response);
    setConfig(response);
    setSavedDocument(stored);
    setPendingDocument(stored);
    setStoredRevision(revisionFromConfig(response));
  }, []);

  const reload = useCallback(async () => {
    adopt(await apiFetch<ConfigResponse>("/api/config"));
  }, [adopt]);

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
  // The server's list, never a save response's `restart_required`: that field
  // is one write's difference against the running generation, while this is
  // measured against what the process booted on and is emptied by the boot
  // that settles it. A setting edited and then put back is on one and not the
  // other, and the banner is answering the question the stored list answers.
  const restartPaths = Array.isArray(config?.restart_paths)
    ? config.restart_paths.filter((path): path is string => typeof path === "string")
    : [];
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

  const changes = changedPaths(pendingDocument, savedDocument);
  const dirty = changes.length > 0;

  /** Throw the pending edit away and go back to what the server holds.
   *
   * Everything the edit produced goes with it: a preview counts a document
   * that no longer exists, and the field errors and the save error were both
   * reported against it. */
  function discard() {
    setPendingDocument(savedDocument);
    setPreview(null);
    setErrors({});
    setSaveError(null);
  }

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
      {/* Deliberately outside the pending panel: a stale save is followed by a
          re-seed that makes that panel disappear, taking the explanation with
          it. */}
      {staleNote !== null && <p className="page-error">{staleNote}</p>}

      {/* Above the tab strip and mounted once, so a restart waiting on a
          setting saved under System is visible from Artwork -- the operator
          who needs to see it is rarely the one still looking at the tab that
          caused it. One mount rather than a second copy inside the System
          panel: two banners on one screen, each with its own Restart button,
          is two answers to a question with one. */}
      <RestartBanner
        paths={restartPaths}
        pendingEdits={dirty}
        onRestarted={reload}
      />

      {/* Replaced by the pending bar the moment there is an edit, which says
          the same thing about a change that exists. */}
      {!dirty && (
        <p className="muted config-note">
          Every setting this service has, a tab at a time. Edits from any tab
          are collected together and nothing is stored until you save.
        </p>
      )}

      {/* Plain buttons, so Tab moves between them and Enter and Space
          activate them without a keydown handler. No roving tabindex and no
          arrow keys: that trade puts every tab in the tab order, which is
          more keystrokes to cross the bar but nothing unreachable, and it is
          the behaviour a reader gets from the markup rather than from a
          handler they have to find. */}
      <div
        className="settings-tabs"
        role="tablist"
        aria-label="Settings sections"
      >
        {TABS.map((entry) => (
          <button
            key={entry.id}
            id={`settings-tab-${entry.id}`}
            type="button"
            role="tab"
            aria-selected={tab === entry.id}
            aria-controls={TAB_PANEL_ID}
            className={tab === entry.id ? "settings-tab current" : "settings-tab"}
            onClick={() => setTab(entry.id)}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {/* One panel that swaps its contents rather than seven, so it keeps one
          id -- every tab points at it, and it points back at whichever tab is
          selected. */}
      <div
        id={TAB_PANEL_ID}
        role="tabpanel"
        aria-labelledby={`settings-tab-${tab}`}
      >
        {config === null && <p className="muted">Loading…</p>}
        {/* The cards, the Add a server row and the library map. Mounted only
            once the configuration has been served, because the five
            server-specific switches the cards show are leaves of that
            document and a card mounted before it arrived would show every one
            of them off -- and because the listing this tab reads is a second
            request, which must not be in flight before the page knows whether
            its own read succeeded.

            It takes `onChanged` rather than the editor: a server saves on its
            own, never joining the pending change, so what it needs from the
            page is the re-read that follows its write. */}
        {config !== null && tab === "servers" && (
          <ServersTab
            config={config}
            revision={storedRevision}
            onChanged={reload}
          />
        )}
        {config !== null && tab === "libraries" && (
          <LibraryOverridesPanel config={config} editor={editor} />
        )}
        {config !== null && (
          <ConfigSections
            config={config}
            editor={editor}
            tab={tab}
            openSection={openSection}
            onOpen={setOpenSection}
          />
        )}
        {config !== null && tab === "system" && (
          <ConfigSafetyPanel
            revision={storedRevision}
            pendingEdits={dirty}
            onChanged={reload}
          />
        )}
        {/* The mounted file's whole remaining job, on the tab that owns the
            rest of the deployment's plumbing. It renders nothing at all when
            the file agrees with the store, or when there is no file. */}
        {config !== null && tab === "system" && (
          <DriftNotice
            revision={storedRevision}
            pendingEdits={dirty}
            onChanged={reload}
          />
        )}
        {/* Not gated on the config load: this accordion fetches its own names
            and sources, and rotating the webhook secret -- which it carries --
            is how an operator recovers a deployment whose config read is what
            failed. The descriptions are whatever the served configuration had,
            and none at all while it is still loading. */}
        {tab === "system" && (
          <SecretsPanel
            descriptions={config === null ? {} : describedFields(config)}
            open={openSection === SECRETS_SECTION}
            onToggle={() =>
              setOpenSection(
                openSection === SECRETS_SECTION ? null : SECRETS_SECTION,
              )
            }
          />
        )}
        {/* TMDB's and TheTVDB's notices are a licence condition of showing
            their artwork and metadata, so they sit on the tab where that
            lives -- and, for the same reason, they do not wait for a config
            load that may never succeed. */}
        {tab === "artwork" && (
          <section className="panel attribution">
            <ProviderAttribution />
          </section>
        )}
      </div>

      {result !== null && (
        <section className="panel config-result">
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
          {/* Neither `restart_required` nor `inert` is repeated here. The
              store carries both of them on `restart_paths`, the re-read above
              has just fetched it, and the banner at the top of the page is
              rendering it -- with the button that acts on it. A second list
              beside that one would be a different measurement of the same
              thing (this save against the running generation, rather than
              every save since the boot) and would disagree with it the first
              time a setting was edited and put back. The preview still shows
              both, because nothing is stored for the banner to read yet. */}
        </section>
      )}

      {dirty && (
        <section className="panel config-pending">
          <h2>Pending changes</h2>
          {/* The difference, not the document. The document is the whole
              configuration, so rendering it would bury one edit in several
              hundred unchanged settings. */}
          <PendingDiff changes={changes} sentinel={keep.sentinel} />
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
                  {`Takes effect at the next restart: ${(preview.inert ?? []).join(", ")}`}
                </p>
              )}
            </>
          )}
          {/* The two commits differ in what happens to the artwork, not in
              what gets stored, and that is the whole of the choice the bar
              below is offering. */}
          <p className="muted config-actions-note">
            Save stores the change and leaves the artwork alone — the drift
            sweep and the full pass pick the new fingerprints up in their own
            time. Save and re-render stores it and queues the affected items
            straight away.
          </p>
        </section>
      )}

      {dirty && (
        <PendingBar
          tabsWithEdits={tabsWithPendingEdits(pendingDocument, savedDocument)}
          busy={busy}
          onDiscard={discard}
          onPreview={() => void submit("preview")}
          onSave={() => void submit("save")}
          onApply={() => void submit("apply")}
        />
      )}
    </>
  );
}
