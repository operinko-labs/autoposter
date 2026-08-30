import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "../api/client";
// The overrides document and its helpers are shared with the settings page
// and the catalog picker -- see `api/overrides.ts` for why a copy of the
// seeding rule would be a bug rather than a duplication.
import {
  documentFromConfig,
  fieldErrors,
  hasPath,
  withPath,
  withoutPath,
} from "../api/overrides";
import type {
  CatalogGroup,
  CollectionsCatalogResponse,
  ConfigResponse,
  ConfigSaveResponse,
  OverridesDocument,
} from "../api/types";
import "./groups.css";

/** Where the panel's order setting is written. A save is always the COMPLETE
 * permutation of every key the server enumerated: partial-list semantics
 * exist server-side, but an explicit full list is what an operator should
 * find persisted in their overrides. Reset is the key going away entirely --
 * the overrides contract's revert -- never a write of any list. */
const GROUP_ORDER_PATH = "collections.group_order";

/** The style select's path. Like the order, what is stored is exactly what
 * the operator sees selected. */
const STYLE_PATH = "collections.separator_style";

/** The style's churn disclosure -- the key is part of every divider's
 * definition hash, so a change re-writes and re-posters each divider once. */
const STYLE_NOTE =
  "Changing the style re-writes and re-posters every divider once on the " +
  "next pass, then settles. It drives both art kinds: fetched art comes " +
  "from the style's folder, generated art from its textless base layer.";

/** Upstream's own contact sheet per style, under `Default-Images/separators`.
 * The preview surface is that image, not a client-side rendering of one: this
 * component knows the URL shape and nothing else about what a style looks
 * like. */
const GRID_BASE =
  "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master/separators";

/** When a saved order is seen. The collections section is live (config live
 * swap; only `collections.enabled` is frozen), so the save lands in the
 * running process at once -- and the tab itself changes when the reconcile
 * pass next runs, which Diff now on this page makes immediate. */
const LIVE_NOTE =
  "A saved order is live in the running process at once — no restart. The " +
  "collections tab itself changes when the reconcile pass next runs; Diff " +
  "now above runs one immediately.";

/** What that pass does. Section numbers derive from position, so reordering
 * renumbers -- a one-off sort-title write per collection in the groups that
 * moved, the row-49 disclosure made operator-facing. */
const CHURN_NOTE =
  "Section numbers derive from position, so the pass that first sees a new " +
  "order re-writes the sort title of every collection in the groups that " +
  "moved — one write per collection, once — and then settles.";

/** What Reset actually reverts to. Removing the key hands the decision back
 * to the mounted config file, which is not always the built-in order -- the
 * file may name its own -- so the copy says which, rather than promising
 * "the default". */
const RESET_NOTE =
  "Reset removes the override: the order goes back to whatever the mounted " +
  "config file says, which is the built-in order when the file says nothing.";

/** The group blocks in Plex's collections tab, in the order the running
 * config shows them, and the buttons that reorder them.
 *
 * Everything rendered here comes off the server's `groups` array -- keys,
 * titles, section numbers. The component holds no group name of its own, so
 * a group the server grows (or renames) appears here without this file being
 * touched: the same rule the catalog picker's tab strip follows for
 * categories, applied to the config-legal keys themselves.
 */
export function GroupsPanel() {
  const [groups, setGroups] = useState<CatalogGroup[] | null>(null);
  // The pending order, as keys. Seeded from the response (which arrives in
  // effective order) and re-seeded after every save: what is stored is the
  // server's answer, not this component's memory of what was clicked.
  const [order, setOrder] = useState<string[]>([]);
  // The style names the server enumerates, and the two halves of the
  // selection: what the running config says, and what is pending here. Both
  // re-seed from the response after a save, exactly as the order does.
  const [styles, setStyles] = useState<string[]>([]);
  const [savedStyle, setSavedStyle] = useState("orig");
  const [style, setStyle] = useState("orig");
  // The overrides the server already holds. Kept whole rather than reduced
  // to the one path: a save here must not drop an override another page
  // stored.
  const [stored, setStored] = useState<OverridesDocument>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Separate from `saveError` on purpose: a re-read that fails after the
  // store succeeded did not unsave anything, and reporting it as a save
  // failure would put a red error beside "Saved."
  const [reloadError, setReloadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ConfigSaveResponse | null>(null);
  const [saving, setSaving] = useState(false);

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

  // A row moved to an edge disables the button that took it there, and a
  // disabled element drops keyboard focus on the floor -- so the click
  // handler queues a hand-off to the row's other button, applied after the
  // re-render. The explicit-focus posture is CatalogPanel's roving-tabindex
  // precedent, applied to the one place this panel needs it.
  const buttonRefs = useRef<
    Record<string, { up: HTMLButtonElement | null; down: HTMLButtonElement | null }>
  >({});
  const pendingFocus = useRef<{ key: string; which: "up" | "down" } | null>(null);
  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    pendingFocus.current = null;
    buttonRefs.current[target.key]?.[target.which]?.focus();
  });

  const adopt = useCallback(
    (catalog: CollectionsCatalogResponse, config: ConfigResponse) => {
      setGroups(catalog.groups);
      setOrder(catalog.groups.map((group) => group.key));
      setStyles(catalog.separator_styles);
      setSavedStyle(catalog.separator_style);
      setStyle(catalog.separator_style);
      setStored(documentFromConfig(config));
    },
    [],
  );

  const reload = useCallback(async () => {
    const [catalog, config] = await Promise.all([
      apiFetch<CollectionsCatalogResponse>("/api/collections/catalog"),
      apiFetch<ConfigResponse>("/api/config"),
    ]);
    if (live.current) adopt(catalog, config);
  }, [adopt]);

  useEffect(() => {
    reload().catch((caught: Error) => {
      if (live.current) setLoadError(caught.message);
    });
  }, [reload]);

  if (loadError !== null) {
    return (
      <section className="panel groups-panel">
        <h2>Groups</h2>
        <p className="page-error">{loadError}</p>
      </section>
    );
  }

  if (groups === null) {
    return (
      <section className="panel groups-panel">
        <h2>Groups</h2>
        <p className="muted">Loading…</p>
      </section>
    );
  }

  const byKey = new Map(groups.map((group) => [group.key, group]));
  const dirty = order.some((key, index) => key !== groups[index]?.key);
  const styleDirty = style !== savedStyle;
  const overridden = hasPath(stored, GROUP_ORDER_PATH);
  const styleOverridden = hasPath(stored, STYLE_PATH);

  /** The same staleness rule `move` applies, for the select: a save panel or a
   * 422 left standing beside a changed selection would read as though it had
   * accounted for the change. */
  function clearSaveState() {
    setResult(null);
    setReloadError(null);
    setErrors({});
    setSaveError(null);
  }

  function move(key: string, delta: -1 | 1) {
    // A stale save panel beside a changed order would read as though that
    // save had accounted for the change; a 422 pinned to an order nobody is
    // proposing any more is worse.
    clearSaveState();
    const index = order.indexOf(key);
    const target = index + delta;
    if (index === -1 || target < 0 || target >= order.length) return;
    if (target === 0) pendingFocus.current = { key, which: "down" };
    else if (target === order.length - 1) pendingFocus.current = { key, which: "up" };
    else pendingFocus.current = null;
    const next = [...order];
    [next[index], next[target]] = [next[target], next[index]];
    setOrder(next);
  }

  /** One PUT for both buttons: Save sends the full permutation at the path,
   * Reset sends the document without it. Everything else -- the 422 handling,
   * the re-read, the stale note -- is identical, so it lives once. */
  async function put(document: OverridesDocument) {
    setSaving(true);
    setErrors({});
    setSaveError(null);
    setReloadError(null);
    setResult(null);
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
          setSaveError("The server rejected this order.");
        } else {
          setSaveError((caught as Error).message);
        }
      }
    }
    // Re-read rather than assume: the stored order and the section numbers
    // are the config's answer to what was just written. Outside the save's
    // own try on purpose -- the store already succeeded, so a failure here
    // means the panel is stale, not that nothing was saved.
    if (saved) {
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
    if (live.current) setSaving(false);
  }

  return (
    <section className="panel groups-panel">
      <div className="groups-header">
        <h2>Groups</h2>
        <div className="groups-actions">
          <button
            type="button"
            disabled={!overridden || saving}
            title={
              overridden
                ? undefined
                : "No override is stored; the mounted config file's order is already in effect."
            }
            onClick={() => void put(withoutPath(stored, GROUP_ORDER_PATH))}
          >
            Reset to config file
          </button>
          <button
            type="button"
            disabled={!(dirty || styleDirty) || saving}
            // One PUT for whichever of the two is dirty: two saves would make
            // the second overwrite a document the first had already changed
            // under it.
            onClick={() => {
              let document = stored;
              if (dirty) document = withPath(document, GROUP_ORDER_PATH, order);
              if (styleDirty) document = withPath(document, STYLE_PATH, style);
              void put(document);
            }}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <p className="muted groups-note">
        The order of the collection blocks in Plex's collections tab. Move a
        group and Save — the whole list is stored, so what you see here is
        exactly what persists as <span className="mono">{GROUP_ORDER_PATH}</span>.
      </p>
      <p className="muted groups-note">{LIVE_NOTE}</p>
      <p className="muted groups-note">{CHURN_NOTE}</p>
      <p className="muted groups-note">{RESET_NOTE}</p>

      <div className="groups-style">
        <label htmlFor="separator-style">Divider style</label>
        <select
          id="separator-style"
          value={style}
          disabled={saving}
          onChange={(event) => {
            clearSaveState();
            setStyle(event.target.value);
          }}
        >
          {styles.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={!styleOverridden || saving}
          title={
            styleOverridden
              ? undefined
              : "No style override is stored; the mounted config file's value is already in effect."
          }
          onClick={() => void put(withoutPath(stored, STYLE_PATH))}
        >
          Reset style
        </button>
        <p className="muted groups-note">{STYLE_NOTE}</p>
        {/* Upstream's own contact sheet for the selected style -- the whole
            preview surface, no client-side rendering. */}
        <img
          className="groups-style-preview"
          src={`${GRID_BASE}/${style}/!_${style}_grid.webp`}
          alt={`${style} separator style preview`}
          loading="lazy"
        />
      </div>

      {(dirty || styleDirty) && (
        <span className="groups-unsaved">unsaved — Save to store</span>
      )}

      <ol className="groups-rows" aria-label="Collection group order">
        {order.map((key, index) => {
          const group = byKey.get(key);
          if (group === undefined) return null;
          return (
            <li key={key} className="groups-row">
              <span className="groups-position muted">{index + 1}.</span>
              <span className="groups-title">{group.title}</span>
              <span className="mono muted groups-key">{key}</span>
              {/* The section number the RUNNING config gives this group --
                  what its sort titles carry in Plex right now. Deliberately
                  not recomputed for a pending move: renumbering happens on
                  save (the churn note says so), and a client-side copy of
                  the position-times-ten arithmetic would be a second source
                  of truth waiting to drift. */}
              <span className="mono muted groups-section">{`!${group.section}`}</span>
              <span className="groups-moves">
                <button
                  type="button"
                  aria-label={`Move ${group.title} up`}
                  disabled={saving || index === 0}
                  ref={(element) => {
                    (buttonRefs.current[key] ??= { up: null, down: null }).up = element;
                  }}
                  onClick={() => move(key, -1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  aria-label={`Move ${group.title} down`}
                  disabled={saving || index === order.length - 1}
                  ref={(element) => {
                    (buttonRefs.current[key] ??= { up: null, down: null }).down = element;
                  }}
                  onClick={() => move(key, 1)}
                >
                  ↓
                </button>
              </span>
            </li>
          );
        })}
      </ol>

      {saveError !== null && <p className="page-error">{saveError}</p>}
      {Object.entries(errors).length > 0 && (
        <ul className="groups-errors">
          {Object.entries(errors).map(([path, message]) => (
            <li key={path}>
              <span className="mono">{path}</span>{" "}
              <span className="groups-error-message">{message}</span>
            </li>
          ))}
        </ul>
      )}

      {result !== null && (
        <div className="groups-saved" role="status">
          <p>{`Saved. Config ${result.version_before} → ${result.version_after}.`}</p>
          {reloadError !== null && <p className="groups-stale">{reloadError}</p>}
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
