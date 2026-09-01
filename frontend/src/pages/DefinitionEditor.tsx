/** The Edit form for one stored `collections.definitions` entry — roadmap row
 * 138, at its literal scope.
 *
 * One rule makes this safe, and it is the whole component: the edited entry is
 * `{...storedEntry, ...curatedEdits}`. The eleven fields below are the only
 * keys this form may add, change or delete; every other key of the stored
 * entry — `schedule`, `filters`, `params`, `changes_webhook`, the `visible_*`
 * flags, the ten others — rides through untouched. That is why an edit here
 * cannot lose a field the operator wrote in YAML and migrated, and it is why
 * the entry handed in must be the STORED overrides array's own entry and never
 * `GET /api/collections/definitions`' seven-field projection.
 *
 * A curated field is written only when its value differs from the schema's own
 * default; a value equal to the default drops the key, which is the overrides
 * document's revert (`api/overrides.ts`: `null` is a value, absence is the
 * revert). That is semantics-preserving for every field here EXCEPT
 * `libraries`, whose default `None` means "every library in
 * collections.libraries" — a moving target, not a fixed value — so scope has
 * its own explicit control rather than being inferred from all-boxes-checked.
 *
 * Three fields are shown and not edited. `builder` is a registry key (free
 * text would name builders that do not exist). `params` is per-builder shaped,
 * so no generic widget is loss-free — changing it is still remove-and-recreate.
 * `changes_webhook` may embed a token in its path, so it is rendered HOST ONLY,
 * the discipline `notifications.url` gets; it is not editable because a value
 * the page was never shown cannot honestly be edited, and the keep-sentinel
 * that solves that for `notifications.url` refuses to resolve inside a list by
 * design. */
import { useState } from "react";

/** The path prefix the schema's descriptions are published under
 * (`config/descriptions.py` walks `list[Model]` into a `[]` segment). */
const DESCRIPTION_PREFIX = "collections.definitions[].";

/** Plex's own collection display modes, plus the unset state as its own named
 * option — `None` ("leave Plex's setting alone") and `"default"` are two
 * different requests and a single select must not blur them. */
const COLLECTION_MODES = [
  { value: "", label: "leave Plex's setting alone" },
  { value: "default", label: "default" },
  { value: "hide", label: "hide" },
  { value: "hideItems", label: "hideItems" },
  { value: "showItems", label: "showItems" },
];

const EMPTY_SCOPE_NOTE =
  "An empty scope means no library at all, which builds nothing — check a " +
  "library, or turn on every configured library.";

export interface Draft {
  title: string;
  everyLibrary: boolean;
  libraries: Record<string, boolean>;
  summary: string;
  sort: string;
  sync_mode: string;
  limit: string;
  labels: string[];
  label_sync: boolean;
  item_label: string[];
  sort_title: string;
  collection_mode: string;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** The scope checkboxes to offer: the config's own library names, plus any
 * name the stored entry carries that the config no longer lists. Without the
 * union, editing anything at all would silently drop a scope the operator
 * wrote for a library they renamed. */
export function libraryRoster(
  entry: Record<string, unknown>,
  libraries: string[],
): string[] {
  const stored = stringList(entry.libraries);
  return [...libraries, ...stored.filter((name) => !libraries.includes(name))];
}

/** The form's state, seeded from the stored entry. */
export function draftFrom(
  entry: Record<string, unknown>,
  libraries: string[],
): Draft {
  const roster = libraryRoster(entry, libraries);
  const stored = Array.isArray(entry.libraries) ? stringList(entry.libraries) : null;
  return {
    title: text(entry.title),
    // The KEY's presence, not the checkbox pattern: an explicit list naming
    // every library today is a different request from "every library".
    everyLibrary: stored === null,
    libraries: Object.fromEntries(
      roster.map((name) => [name, stored === null ? true : stored.includes(name)]),
    ),
    summary: text(entry.summary),
    sort: text(entry.sort),
    sync_mode: entry.sync_mode === "append" ? "append" : "sync",
    limit: typeof entry.limit === "number" ? String(entry.limit) : "",
    labels: stringList(entry.labels),
    label_sync: entry.label_sync === true,
    item_label: stringList(entry.item_label),
    sort_title: text(entry.sort_title),
    collection_mode: text(entry.collection_mode),
  };
}

/** The stored entry with the curated fields applied — the loss-free law.
 *
 * `next` starts as a copy of the stored entry, so every key this form does not
 * name survives by construction rather than by being listed somewhere. */
export function entryFromDraft(
  entry: Record<string, unknown>,
  draft: Draft,
  roster: string[],
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...entry };
  const put = (key: string, value: unknown, isDefault: boolean) => {
    if (isDefault) delete next[key];
    else next[key] = value;
  };

  next.title = draft.title.trim();

  if (draft.everyLibrary) delete next.libraries;
  else next.libraries = roster.filter((name) => draft.libraries[name]);

  put("summary", draft.summary, draft.summary === "");
  const sort = draft.sort.trim();
  put("sort", sort, sort === "" || sort === "custom");
  put("sync_mode", draft.sync_mode, draft.sync_mode === "sync");
  put("limit", Number(draft.limit), draft.limit.trim() === "");
  const labels = draft.labels.filter((item) => item.trim() !== "");
  put("labels", labels, labels.length === 0);
  put("label_sync", true, !draft.label_sync);
  const itemLabels = draft.item_label.filter((item) => item.trim() !== "");
  put("item_label", itemLabels, itemLabels.length === 0);
  put("sort_title", draft.sort_title, draft.sort_title === "");
  put("collection_mode", draft.collection_mode, draft.collection_mode === "");

  return next;
}

/** A URL reduced to its host — never its path, which is where a token hides
 * (the `notifications.url` rule, `api/routes.py`'s `_host_only`). A value that
 * is not a URL says so rather than being echoed: echoing it would defeat the
 * whole point on the one input shape most likely to be malformed. */
export function hostOnly(value: string): string {
  try {
    const host = new URL(value).host;
    return host === "" ? "(unreadable URL)" : host;
  } catch {
    return "(unreadable URL)";
  }
}

/** Per-item inputs, not a comma-joined string: a joined field cannot
 * represent a label that contains the separator. */
function StringList({
  name,
  addLabel,
  value,
  onChange,
}: {
  name: string;
  addLabel: string;
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <span className="definition-list-edit">
      {value.map((item, index) => (
        <span className="definition-list-item" key={index}>
          <input
            type="text"
            aria-label={`${name}[${index}]`}
            value={item}
            onChange={(event) =>
              onChange(value.map((v, i) => (i === index ? event.target.value : v)))
            }
          />
          <button
            type="button"
            aria-label={`Remove ${name}[${index}]`}
            onClick={() => onChange(value.filter((_, i) => i !== index))}
          >
            ×
          </button>
        </span>
      ))}
      <button type="button" onClick={() => onChange([...value, ""])}>
        {addLabel}
      </button>
    </span>
  );
}

export function DefinitionEditor({
  entry,
  libraries,
  descriptions,
  busy,
  onSave,
  onCancel,
}: {
  entry: Record<string, unknown>;
  libraries: string[];
  descriptions: Record<string, string>;
  busy: boolean;
  onSave: (entry: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const roster = libraryRoster(entry, libraries);
  const [draft, setDraft] = useState<Draft>(() => draftFrom(entry, libraries));
  const set = (patch: Partial<Draft>) =>
    setDraft((previous) => ({ ...previous, ...patch }));

  // Row 138's own promise: the twenty-one fields are already described and
  // already served under a `[]` segment, waiting for a row to hang on.
  const hint = (field: string) => descriptions[DESCRIPTION_PREFIX + field] || undefined;

  const scopeEmpty =
    !draft.everyLibrary && roster.every((name) => !draft.libraries[name]);
  const ready = draft.title.trim() !== "" && !scopeEmpty;

  const webhook = text(entry.changes_webhook);

  return (
    <div className="definition-editor">
      <h4>Edit definition</h4>

      <label className="definition-field" title={hint("title")}>
        <span>Title</span>
        <input
          type="text"
          aria-label="Title"
          value={draft.title}
          onChange={(event) => set({ title: event.target.value })}
        />
      </label>

      <fieldset className="definition-scope">
        <legend className="muted">Libraries</legend>
        <label className="definition-library">
          <input
            type="checkbox"
            aria-label="Every configured library"
            checked={draft.everyLibrary}
            onChange={(event) => set({ everyLibrary: event.target.checked })}
          />
          <span>Every configured library</span>
        </label>
        {roster.map((name) => (
          <label key={name} className="definition-library">
            <input
              type="checkbox"
              aria-label={name}
              disabled={draft.everyLibrary}
              checked={draft.everyLibrary || (draft.libraries[name] ?? false)}
              onChange={(event) =>
                set({ libraries: { ...draft.libraries, [name]: event.target.checked } })
              }
            />
            <span>{name}</span>
          </label>
        ))}
        {scopeEmpty && <p className="definition-refusal">{EMPTY_SCOPE_NOTE}</p>}
      </fieldset>

      <label className="definition-field" title={hint("summary")}>
        <span>Summary</span>
        <input
          type="text"
          aria-label="Summary"
          value={draft.summary}
          onChange={(event) => set({ summary: event.target.value })}
        />
      </label>

      <label className="definition-field" title={hint("sort")}>
        <span>Sort</span>
        <input
          type="text"
          aria-label="Sort"
          value={draft.sort}
          onChange={(event) => set({ sort: event.target.value })}
        />
      </label>

      <label className="definition-field" title={hint("sync_mode")}>
        <span>Sync mode</span>
        <select
          aria-label="Sync mode"
          value={draft.sync_mode}
          onChange={(event) => set({ sync_mode: event.target.value })}
        >
          <option value="sync">sync</option>
          <option value="append">append</option>
        </select>
      </label>

      <label className="definition-field" title={hint("limit")}>
        <span>Limit</span>
        <input
          type="number"
          min={1}
          aria-label="Limit"
          value={draft.limit}
          onChange={(event) => set({ limit: event.target.value })}
        />
      </label>

      <label className="definition-field" title={hint("sort_title")}>
        <span>Sort title</span>
        <input
          type="text"
          aria-label="Sort title"
          value={draft.sort_title}
          onChange={(event) => set({ sort_title: event.target.value })}
        />
      </label>

      <label className="definition-field" title={hint("collection_mode")}>
        <span>Collection mode</span>
        <select
          aria-label="Collection mode"
          value={draft.collection_mode}
          onChange={(event) => set({ collection_mode: event.target.value })}
        >
          {COLLECTION_MODES.map((mode) => (
            <option key={mode.value} value={mode.value}>
              {mode.label}
            </option>
          ))}
        </select>
      </label>

      <div className="definition-field" title={hint("labels")}>
        <span>Labels</span>
        <StringList
          name="labels"
          addLabel="Add label"
          value={draft.labels}
          onChange={(next) => set({ labels: next })}
        />
      </div>

      <label className="definition-field" title={hint("label_sync")}>
        <span>Label sync</span>
        <input
          type="checkbox"
          aria-label="Label sync"
          checked={draft.label_sync}
          onChange={(event) => set({ label_sync: event.target.checked })}
        />
      </label>

      <div className="definition-field" title={hint("item_label")}>
        <span>Member labels</span>
        <StringList
          name="item_label"
          addLabel="Add member label"
          value={draft.item_label}
          onChange={(next) => set({ item_label: next })}
        />
      </div>

      <dl className="definition-readonly">
        <dt title={hint("builder")}>Builder</dt>
        <dd className="mono">{text(entry.builder)}</dd>
        <dt title={hint("params")}>Params</dt>
        <dd className="mono">{JSON.stringify(entry.params)}</dd>
        {webhook !== "" && (
          <>
            <dt title={hint("changes_webhook")}>Change webhook</dt>
            <dd className="mono">{hostOnly(webhook)}</dd>
          </>
        )}
      </dl>

      <div className="definition-actions">
        <button
          type="button"
          disabled={busy || !ready}
          onClick={() => onSave(entryFromDraft(entry, draft, roster))}
        >
          Save
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
