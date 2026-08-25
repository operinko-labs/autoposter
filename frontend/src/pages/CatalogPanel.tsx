import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { ApiError, apiFetch } from "../api/client";
// The overrides document is shared with the settings page, helpers and all --
// see `api/overrides.ts` for why a second copy of the seeding rule would be a
// bug rather than a duplication.
import { documentFromConfig, fieldErrors, withPath } from "../api/overrides";
import type {
  CatalogCategory,
  CatalogPreset,
  CollectionsCatalogResponse,
  ConfigResponse,
  ConfigSaveResponse,
  OverridesDocument,
} from "../api/types";
import "./catalog.css";

/** Where a preset row's choice is written. A setting-backed row writes the
 * path it carries instead; see `CatalogPreset.setting`. */
const PRESETS_PATH = "collections.presets";

/** What a save here does and does not do.
 *
 * The collections section is live (config/live.py), so the running process
 * picks the change up without a restart -- but nothing re-runs on the strength
 * of a config write. The collections appear when the reconcile pass next runs,
 * and Diff now on this page is how an operator makes that immediate. Saying
 * "saved" without saying that leaves them watching Plex for a collection that
 * is not coming until the hour turns. */
export const APPLIES_NOTE =
  "The change is live in the running process, and the collections themselves " +
  "appear at the next reconcile — use Diff now above to run one immediately.";

/** Why the settings page's preview shows no number for a change made here.
 *
 * `POST /api/config/preview` answers `impact: null` for a collections-only
 * edit, and null is not zero: the question "how many rendered images does this
 * invalidate" does not apply to a collection's membership, because no image
 * changes. An operator who read the null as a broken preview would file a bug
 * against correct behaviour, so the panel says which it is. */
export const NO_IMPACT_NOTE =
  "Switching a preset on or off changes no rendered artwork, so the settings " +
  "page's preview reports no artwork impact for a change made here — by " +
  "design, not a missing number.";

/** What a category with no rows says. Deliberately claims no roadmap row: the
 * catalog endpoint reports which *preset* is gated and on what, but says
 * nothing about when an empty category gets filled, and a row number invented
 * here would be a number nothing in the codebase backs. */
const EMPTY_CATEGORY_NOTE =
  "Nothing in this category yet. It is listed because the service has the " +
  "category, not because a row is hiding — presets appear here as they are " +
  "written.";

const GATED = "gated";

function allRows(categories: CatalogCategory[]): CatalogPreset[] {
  return categories.flatMap((category) => category.presets);
}

/** The keys `collections.presets` would carry for a given set of choices, in
 * catalog order.
 *
 * Catalog order rather than the order the operator clicked: the server expands
 * presets by scanning the catalog, so the order of the list is not something a
 * deployment's behaviour depends on, and a list that reshuffled itself per
 * click would show up as a spurious diff in the operator's overrides file.
 *
 * Setting-backed rows are excluded because they are not preset keys at all --
 * the server refuses `presets: [oscars]` as unknown. A gated row cannot reach
 * here either: its checkbox is disabled, and the config refuses its key, so it
 * is never on. */
function presetKeys(
  categories: CatalogCategory[],
  chosen: Record<string, boolean>,
): string[] {
  return allRows(categories)
    .filter((preset) => preset.setting === null && chosen[preset.key])
    .map((preset) => preset.key);
}

function sameKeys(one: string[], two: string[]): boolean {
  return one.length === two.length && one.every((key, index) => key === two[index]);
}

/** The state the server currently holds, as a choice map. */
function storedChoices(categories: CatalogCategory[]): Record<string, boolean> {
  return Object.fromEntries(allRows(categories).map((row) => [row.key, row.active]));
}

/** The document to PUT: the overrides the server already holds, plus this
 * panel's changes and nothing else.
 *
 * Only *changed* rows are written, and that is the load-bearing rule. The
 * document is a delta, so writing every setting-backed row's current value
 * would freeze three booleans as overrides the operator never set -- pinning
 * them against the mounted config file for good. A row the operator did not
 * touch contributes nothing.
 *
 * `collections.presets` is written whole when the chosen set differs, because
 * a list is replaced rather than merged. An operator who unchecked everything
 * gets an explicit `[]`, which is the honest expression of "none": dropping
 * the key would revert to whatever the config file lists, which is not what
 * they asked for. */
function documentToSave(
  stored: OverridesDocument,
  categories: CatalogCategory[],
  chosen: Record<string, boolean>,
): OverridesDocument {
  let document = stored;
  const wanted = presetKeys(categories, chosen);
  if (!sameKeys(wanted, presetKeys(categories, storedChoices(categories)))) {
    document = withPath(document, PRESETS_PATH, wanted);
  }
  for (const row of allRows(categories)) {
    if (row.setting === null) continue;
    if (chosen[row.key] === row.active) continue;
    document = withPath(document, row.setting, chosen[row.key]);
  }
  return document;
}

function buildsLabel(preset: CatalogPreset): string | null {
  const titles = preset.titles.join(", ");
  if (preset.years_title === null) {
    return titles === "" ? null : `Builds: ${titles}`;
  }
  // The dynamic half is reported as a shape, never as titles: which years the
  // dataset carries is not knowable from a table.
  const dynamic = `one per ceremony, named "${preset.years_title}"`;
  return titles === ""
    ? `Builds ${dynamic}`
    : `Builds: ${titles}, plus ${dynamic}`;
}

/** One catalog row: the switch, and everything needed to decide whether to
 * throw it. */
function CatalogRow({
  preset,
  checked,
  error,
  onToggle,
}: {
  preset: CatalogPreset;
  checked: boolean;
  error: string | undefined;
  onToggle: (next: boolean) => void;
}) {
  const gated = preset.readiness === GATED;
  const describedBy = `catalog-description-${preset.key}`;
  const builds = buildsLabel(preset);
  return (
    <li className="catalog-row">
      <label className="catalog-choice">
        <input
          type="checkbox"
          checked={checked}
          // A gated preset's key is refused by the config loader, naming the
          // roadmap row. An enabled checkbox here would offer a save that
          // cannot succeed.
          disabled={gated}
          aria-describedby={describedBy}
          onChange={(event) => onToggle(event.target.checked)}
        />
        <span className="catalog-name">{preset.name}</span>
      </label>
      <div className="catalog-detail">
        <p className="muted catalog-description" id={describedBy}>
          {preset.description}
        </p>
        {builds !== null && <p className="muted catalog-builds">{builds}</p>}
        <p className="catalog-meta">
          <span className={`catalog-badge catalog-${preset.readiness}`}>
            {preset.readiness}
          </span>
          {gated && preset.gated_row !== null && (
            <span className="catalog-badge catalog-needs">
              {`needs row ${preset.gated_row}`}
            </span>
          )}
          {/* Which switch this row throws, spelled out: a setting-backed row
              looks exactly like a preset row otherwise, and the two write
              different halves of the config. */}
          {preset.setting !== null && (
            <span className="catalog-badge catalog-setting mono">{preset.setting}</span>
          )}
          <span className="catalog-source mono">{preset.kometa_source}</span>
          <span className="muted catalog-libraries">
            {preset.library_types.join(", ")}
          </span>
        </p>
        {error !== undefined && (
          <p className="catalog-row-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </li>
  );
}

/** The browsable catalog of preset collections, and the switch for each.
 *
 * The app's first tab component. The semantics are the WAI-ARIA tabs pattern
 * with automatic activation -- selection follows focus -- which is the right
 * half of that choice here because every panel's content is already in hand
 * when the strip renders: nothing is fetched per tab, so arrowing through the
 * categories costs nothing and a keyboard user is not made to press Enter on
 * each one to see it. A future tab strip that loads its panel on demand should
 * take the manual-activation half instead, and this comment is where the
 * difference is recorded.
 *
 * The strip is a single tab stop (roving tabindex): Tab reaches the selected
 * tab and then leaves the strip for the panel, rather than walking through
 * nine buttons. The arrow keys move within it, and they wrap.
 */
export function CatalogPanel() {
  const [categories, setCategories] = useState<CatalogCategory[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  // The overrides the server already holds. Kept whole rather than reduced to
  // the collections section: a save here must not drop an override the
  // settings page stored.
  const [stored, setStored] = useState<OverridesDocument>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ConfigSaveResponse | null>(null);
  const [saving, setSaving] = useState(false);

  // A ref rather than an effect-local `cancelled`: the save handler's
  // continuation lands outside any effect, the same reasoning the neighbouring
  // panels use.
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  /** Take the server's word for what is switched on. Called on mount and again
   * after every save: which keys are active is the config's answer, not this
   * component's memory of what was clicked. */
  const adopt = useCallback(
    (catalog: CollectionsCatalogResponse, config: ConfigResponse) => {
      setCategories(catalog.categories);
      setChosen(storedChoices(catalog.categories));
      setStored(documentFromConfig(config));
      setSelected((current) => current ?? catalog.categories[0]?.key ?? null);
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
      <section className="panel catalog-panel">
        <h2>Catalog</h2>
        <p className="page-error">{loadError}</p>
      </section>
    );
  }

  if (categories === null || selected === null) {
    return (
      <section className="panel catalog-panel">
        <h2>Catalog</h2>
        <p className="muted">Loading…</p>
      </section>
    );
  }

  const rows = allRows(categories);
  const dirty = rows.some((row) => chosen[row.key] !== row.active);
  const activeTotal = rows.filter((row) => chosen[row.key]).length;
  const perCategory = categories
    .map((category) => ({
      label: category.label,
      count: category.presets.filter((row) => chosen[row.key]).length,
    }))
    .filter((entry) => entry.count > 0);
  const current = categories.find((category) => category.key === selected);

  function move(index: number) {
    const category = categories![index];
    setSelected(category.key);
    // Focus follows selection: the tab the arrow key chose is the one the
    // keyboard is now on, so the next arrow press continues from there.
    tabRefs.current[category.key]?.focus();
  }

  function onTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = categories!.length - 1;
    let next: number | null = null;
    if (event.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (event.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next === null) return;
    // The arrow keys scroll the page by default, which would move the view out
    // from under the panel the operator is arrowing through.
    event.preventDefault();
    move(next);
  }

  function toggle(key: string, next: boolean) {
    // A stale save panel beside a changed set of choices would read as though
    // that save had accounted for the change.
    setResult(null);
    setChosen((previous) => ({ ...previous, [key]: next }));
  }

  async function save() {
    setSaving(true);
    setErrors({});
    setSaveError(null);
    setResult(null);
    try {
      const response = await apiFetch<ConfigSaveResponse>("/api/config/overrides", {
        method: "PUT",
        body: JSON.stringify({
          document: documentToSave(stored, categories!, chosen),
        }),
      });
      if (live.current) setResult(response);
      // Re-read rather than assume: the catalog's `active` states are the
      // config's answer to what was just stored, and the document has to be
      // re-seeded from the paths the server now says are overridden.
      await reload();
    } catch (caught) {
      if (!live.current) return;
      if (caught instanceof ApiError && caught.status === 422) {
        setErrors(fieldErrors(caught.detail));
        setSaveError("The server rejected these choices.");
      } else {
        setSaveError((caught as Error).message);
      }
    } finally {
      if (live.current) setSaving(false);
    }
  }

  /** A field error whose path names this row: the setting-backed row's own
   * path, or nothing. Errors against `collections.presets` are about the list
   * rather than any one row, so they render once, below. */
  function rowError(preset: CatalogPreset): string | undefined {
    return preset.setting === null ? undefined : errors[preset.setting];
  }

  const listErrors = Object.entries(errors).filter(
    ([path]) => !rows.some((row) => row.setting === path),
  );

  return (
    <section className="panel catalog-panel">
      <div className="catalog-header">
        <h2>Catalog</h2>
        <button type="button" disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>

      <p className="muted catalog-note">
        Every collection this service can build. Switching one on adds its
        definitions to the pass; everything here is off until you say otherwise.
      </p>
      <p className="muted catalog-note">{NO_IMPACT_NOTE}</p>

      <div className="catalog-summary" role="group" aria-label="What is switched on">
        <span className="catalog-total">{`${activeTotal} active`}</span>
        {perCategory.map((entry) => (
          <span key={entry.label} className="catalog-count">
            {`${entry.label} ${entry.count}`}
          </span>
        ))}
        {dirty && <span className="catalog-unsaved">unsaved — Save to store</span>}
      </div>

      <div className="catalog-tabs" role="tablist" aria-label="Catalog categories">
        {categories.map((category, index) => (
          <button
            key={category.key}
            type="button"
            role="tab"
            id={`catalog-tab-${category.key}`}
            className="catalog-tab"
            aria-selected={category.key === selected}
            aria-controls={`catalog-tabpanel-${category.key}`}
            // One tab stop for the whole strip; the arrow keys move within it.
            tabIndex={category.key === selected ? 0 : -1}
            ref={(element) => {
              tabRefs.current[category.key] = element;
            }}
            onKeyDown={(event) => onTabKeyDown(event, index)}
            onClick={() => setSelected(category.key)}
          >
            {category.label}
          </button>
        ))}
      </div>

      {current !== undefined && (
        <div
          role="tabpanel"
          id={`catalog-tabpanel-${current.key}`}
          aria-labelledby={`catalog-tab-${current.key}`}
          className="catalog-tabpanel"
          // The panel is the strip's next tab stop, and it scrolls, so it has
          // to be reachable by keyboard in its own right.
          tabIndex={0}
        >
          {current.presets.length === 0 ? (
            <p className="empty">{EMPTY_CATEGORY_NOTE}</p>
          ) : (
            <ul className="catalog-rows">
              {current.presets.map((preset) => (
                <CatalogRow
                  key={preset.key}
                  preset={preset}
                  checked={chosen[preset.key] ?? false}
                  error={rowError(preset)}
                  onToggle={(next) => toggle(preset.key, next)}
                />
              ))}
            </ul>
          )}
        </div>
      )}

      {saveError !== null && <p className="page-error">{saveError}</p>}
      {listErrors.length > 0 && (
        <ul className="catalog-errors">
          {listErrors.map(([path, message]) => (
            <li key={path}>
              <span className="mono catalog-error-path">{path}</span>{" "}
              <span className="catalog-error-message">{message}</span>
            </li>
          ))}
        </ul>
      )}

      {result !== null && (
        <div className="catalog-saved" role="status">
          <p>
            {`Saved. Config ${result.version_before} → ${result.version_after}.`}
          </p>
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
