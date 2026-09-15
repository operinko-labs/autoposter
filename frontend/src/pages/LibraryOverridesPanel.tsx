/** The per-library override matrix (roadmap row 92).
 *
 * Rows are Plex libraries, columns are the settings a library may do
 * differently, and every cell is one of three states: inheriting the global,
 * set to something else, or not editable here. It is the section's only home
 * on the page now: a generic accordion over `libraries` used to sit beneath
 * it, and it was an empty accordion on a deployment that overrides nothing
 * and a second, differently shaped copy of this table on one that does. What
 * that tree could do and this cannot is edit a per-library LIST or MAPPING
 * cell -- those are reported here and set through the API, which is the cost
 * of the section having one home instead of two.
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
import type { Editor } from "./Settings";

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

function libraryNames(config: ConfigResponse): string[] {
  const names = readPath(config, "collections.libraries");
  return Array.isArray(names) ? names.filter((n): n is string => typeof n === "string") : [];
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
  editor,
}: {
  library: string;
  suffix: string;
  globalValue: unknown;
  editor: Editor;
}) {
  const path = `libraries.${library}.${suffix}`;
  const set = hasPath(editor.document, path);
  const current = set ? readPath(editor.document, path) : undefined;
  const inherits = `inherits ${describeGlobal(globalValue)}`;

  // A boolean is the only leaf type with a closed set of answers, so it gets
  // the widget that can say "unset" as a first-class third option -- which a
  // checkbox cannot, and which is the state this whole panel is about.
  if (typeof globalValue === "boolean") {
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

  // A list or a mapping needs a list editor, and a copy of the settings
  // page's inside a table cell is exactly the shape `api/overrides.ts`'s own
  // docstring warns about. The cell reports its state instead, and the
  // panel's copy says so rather than implying an edit that is not here.
  if (Array.isArray(globalValue) || isPlainObject(globalValue)) {
    return (
      <span className="muted library-cell-note">
        {set ? "overridden" : inherits}
      </span>
    );
  }

  // Everything else is a string or a null -- the five `Literal | None`
  // sources, whose global default is null. No numeric branch: not one leaf
  // in the whitelist is a number.
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
  // Nothing to override and nowhere to put it: a deployment with no
  // configured library gets no panel rather than an empty table.
  if (libraries.length === 0 || leaves.length === 0) return null;

  const descriptions = isPlainObject(config.field_descriptions)
    ? config.field_descriptions
    : {};

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
        List and mapping settings (ignore lists, overlay families, the genre
        and content-rating mappers, field verbs) say here whether a library
        overrides the global, but are not edited on this page.
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
