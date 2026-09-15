/** The per-library override matrix (roadmap row 92).
 *
 * Rows are Plex libraries, columns are the settings a library may do
 * differently, and every cell is one of three states: inheriting the global,
 * set to something else, or not editable here. It is the section's only home
 * on the page now: a generic accordion over `libraries` used to sit beneath
 * it, and it was an empty accordion on a deployment that overrides nothing
 * and a second, differently shaped copy of this table on one that does. A
 * list cell is edited here rather than there (the tree could only ever edit a
 * per-library list that already HELD a value -- it was shape-driven, so an
 * unset one was `null` and got no control); a MAPPING cell is reported and
 * not edited, which is the one thing this page cannot do that it could.
 *
 * It writes through the page's own `Editor` rather than fetching anything,
 * and that is the decision everything else falls out of. One pending
 * document, so the page's one pending bar -- Preview impact, Save, Save and
 * re-render -- carries these edits along with every other tab's. The
 * document helpers, so setting writes exactly one dotted path and clearing
 * DELETES it -- `withoutPath` prunes an emptied branch, so a library row can
 * never be left behind as the `{}` the API refuses. The page's revision
 * token, so a stale save is the same 409 it is anywhere else. And the
 * existing drop cap, so removing a populated row is refused with the same
 * message and the same `confirm: true` remedy as any other destructive save
 * -- this panel adds no cap of its own and no confirm control of its own.
 *
 * THE FREEZING HAZARD, in this panel's own vocabulary: clearing a cell must
 * delete the key, never write a value equal to today's global. Writing
 * `libraries.Anime.badges.enabled: true` because that is what the global
 * says now would freeze this library at today's value, and the next edit to
 * the deployed YAML would silently stop reaching it. `LibraryOverridesPanel.
 * test.tsx` pins it by name.
 *
 * The whitelist is DERIVED, never listed here: `field_descriptions` carries
 * exactly one entry per overridable leaf, under a `libraries.{}.` wildcard
 * (`config/descriptions.py`), so a setting the schema stops accepting stops
 * appearing without anybody having to remember this file exists.
 */
import type { ConfigResponse } from "../api/types";
import { hasPath, isPlainObject, readPath } from "../api/overrides";
import { StringListField, type Editor } from "./Settings";

/** The prefix `config/descriptions.py` publishes the per-library shape under.
 * `{}` stands in for a library NAME, which is data a schema walk cannot
 * enumerate -- the same role `[]` plays for a list of objects. */
export const LIBRARY_WILDCARD = "libraries.{}.";

/** The overridable leaves, in served order, as `<section>.<field>` suffixes.
 *
 * Filtered out: the section headings (`libraries.{}.operations`), which the
 * walk describes like any other field but which are not cells.
 */
export function overridableLeaves(config: ConfigResponse): string[] {
  const descriptions = config.field_descriptions;
  if (!isPlainObject(descriptions)) return [];
  return Object.keys(descriptions)
    .filter((path) => path.startsWith(LIBRARY_WILDCARD))
    .map((path) => path.slice(LIBRARY_WILDCARD.length))
    .filter((suffix) => suffix.includes("."))
    .sort();
}

/** The libraries this table has a column for: the ones collections are built
 * in, plus every library that already overrides something.
 *
 * The union, not `collections.libraries` alone. That list is the scope of ONE
 * feature, and per-library overrides cover badges, operations and maintenance,
 * which apply to libraries outside it -- so an override stored under a library
 * that is not in it (or one later removed from it) had no column, no row and,
 * since this panel became the section's only home, no rendering at all: stored,
 * still applied by the service, and invisible. A library that overrides
 * something is exactly a library this table should show.
 *
 * Sorted, because the two sources have no shared order to preserve. */
function libraryNames(config: ConfigResponse): string[] {
  const scoped = readPath(config, "collections.libraries");
  const stored = config.libraries;
  const names = new Set<string>(
    Array.isArray(scoped) ? scoped.filter((n): n is string => typeof n === "string") : [],
  );
  if (isPlainObject(stored)) for (const name of Object.keys(stored)) names.add(name);
  return [...names].sort();
}

/** The kind the schema declares for one overridable leaf, read under the
 * wildcard the map publishes it at.
 *
 * The cell used to pick its control from the GLOBAL value, which is the same
 * defect this branch fixes on the settings page: a leaf whose global is unset
 * is `null`, which says nothing about what it holds. */
function leafKinds(config: ConfigResponse): Record<string, string> {
  const kinds = config.field_types;
  if (!isPlainObject(kinds)) return {};
  return Object.fromEntries(
    Object.entries(kinds)
      .filter(([path]) => path.startsWith(LIBRARY_WILDCARD))
      .map(([path, kind]) => [path.slice(LIBRARY_WILDCARD.length), String(kind)]),
  );
}

/** How the global value reads in a placeholder. Deliberately the same
 * vocabulary the settings page's own `ScalarValue` uses, so "on" here and
 * "on" there mean one thing. */
function describeGlobal(value: unknown): string {
  if (typeof value === "boolean") return value ? "on" : "off";
  if (Array.isArray(value)) return value.length === 0 ? "(none)" : value.join(", ");
  if (isPlainObject(value)) {
    const keys = Object.keys(value);
    return keys.length === 0 ? "(none)" : `${keys.length} entries`;
  }
  if (value === null || value === "") return "(not set)";
  return String(value);
}

function Cell({
  library,
  suffix,
  globalValue,
  kind,
  editor,
}: {
  library: string;
  suffix: string;
  globalValue: unknown;
  /** What the schema says this leaf holds, from `field_types` under the
   * wildcard. `undefined` only for a server that serves no kinds, where the
   * global's own type is the best answer available -- which is what this cell
   * read before, and is wrong for exactly the leaves whose global is unset. */
  kind?: string;
  editor: Editor;
}) {
  const path = `libraries.${library}.${suffix}`;
  const set = hasPath(editor.document, path);
  const current = set ? readPath(editor.document, path) : undefined;
  const inherits = `inherits ${describeGlobal(globalValue)}`;

  // A boolean has a closed set of answers, so it gets the widget that can say
  // "unset" as a first-class third option -- which a checkbox cannot, and
  // which is the state this whole panel is about.
  if (kind === undefined ? typeof globalValue === "boolean" : kind === "boolean") {
    return (
      <>
        <select
          aria-label={path}
          title={inherits}
          value={set ? (current === true ? "on" : "off") : "inherit"}
          onChange={(event) => {
            const choice = event.target.value;
            if (choice === "inherit") editor.clear(path);
            else editor.setValue(path, choice === "on");
          }}
        >
          <option value="inherit">inherit</option>
          <option value="on">on</option>
          <option value="off">off</option>
        </select>
        {!set && <span className="muted library-cell-note">{inherits}</span>}
      </>
    );
  }

  // A list of strings is edited here, with the settings page's own list
  // field rather than a copy of it -- a copy is the shape `api/overrides.ts`'s
  // own docstring warns about. An empty cell writes nothing, so the override
  // is created by the first entry added; "inherit" deletes the key, the same
  // removal the three-state select performs, so the library goes back to
  // following the global rather than freezing today's value.
  if (kind === "string_list") {
    return (
      <>
        <StringListField
          path={path}
          value={(Array.isArray(current) ? current : []).map(String)}
          onChange={(next) => editor.setValue(path, next)}
        />
        {set ? (
          <button
            type="button"
            aria-label={`Inherit ${path}`}
            onClick={() => editor.clear(path)}
          >
            Inherit
          </button>
        ) : (
          <span className="muted library-cell-note">{inherits}</span>
        )}
      </>
    );
  }

  // A mapping has no single control, so the cell reports its state and the
  // panel's copy says so rather than implying an edit that is not here.
  if (
    kind === undefined
      ? Array.isArray(globalValue) || isPlainObject(globalValue)
      : kind === "object"
  ) {
    return (
      <span className="muted library-cell-note">
        {set ? "overridden" : inherits}
      </span>
    );
  }

  // Everything else is a string -- including the `Literal | None` sources,
  // whose global default is null and whose kind the schema still answers. No
  // numeric branch: not one leaf in the whitelist is a number.
  return (
    <input
      type="text"
      aria-label={path}
      title={inherits}
      placeholder={inherits}
      value={typeof current === "string" ? current : ""}
      onChange={(event) => {
        const raw = event.target.value;
        // An emptied box is "inherit", not "the empty string": clearing has
        // to be expressible, and the empty string is a value the API would
        // validate like any other.
        if (raw === "") editor.clear(path);
        else editor.setValue(path, raw);
      }}
    />
  );
}

export function LibraryOverridesPanel({
  config,
  editor,
}: {
  config: ConfigResponse;
  editor: Editor;
}) {
  const libraries = libraryNames(config);
  const leaves = overridableLeaves(config);
  // Nothing to override and nowhere to put it: a deployment with no library
  // in scope and none overriding anything gets no panel rather than an empty
  // table.
  if (libraries.length === 0 || leaves.length === 0) return null;

  const descriptions = isPlainObject(config.field_descriptions)
    ? config.field_descriptions
    : {};
  const kinds = leafKinds(config);

  return (
    <section className="panel config-section library-overrides">
      <h2>Per-library overrides</h2>
      <p className="muted config-note">
        Each library uses the global setting unless it says otherwise here.
        Choosing <strong>inherit</strong> removes the setting from this
        library rather than copying today&apos;s value into it, so a later
        change to the deployed configuration still reaches it. This panel
        changes no rendered artwork — only how items in a library are written
        to Plex, badged and swept.
      </p>
      <p className="muted config-note">
        Lists (the ignore lists, overlay families) are set here entry by
        entry, and <strong>Inherit</strong> beside one removes it. Mapping
        settings — the genre and content-rating mappers, field verbs — say
        here whether a library overrides the global, but are not edited on
        this page.
      </p>
      <div className="library-overrides-scroll">
        <table className="library-overrides-table">
          <thead>
            <tr>
              <th scope="col">Setting</th>
              {libraries.map((library) => (
                <th scope="col" key={library}>{library}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {leaves.map((suffix) => (
              <tr key={suffix}>
                <th scope="row" title={String(descriptions[`${LIBRARY_WILDCARD}${suffix}`] ?? "")}>
                  {suffix}
                </th>
                {libraries.map((library) => (
                  <td key={library} data-testid={`cell-${library}-${suffix}`}>
                    <Cell
                      library={library}
                      suffix={suffix}
                      globalValue={readPath(config, suffix)}
                      kind={kinds[suffix]}
                      editor={editor}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
