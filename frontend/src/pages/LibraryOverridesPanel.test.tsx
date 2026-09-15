import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LibraryOverridesPanel, overridableLeaves } from "./LibraryOverridesPanel";

/** A served `GET /api/config`, cut down to what this panel reads: the global
 * values it takes its widgets and placeholders from, the library names, and
 * the `libraries.{}.` description entries that ARE the whitelist. */
const CONFIG = {
  operations: {
    enabled: true,
    write_to_plex: true,
    user_rating_source: null,
    // A global nobody has set, on a leaf that holds a boolean: the cell used
    // to pick its control from this value, so it gave this one a text box.
    lock_apply: null,
    ignore_labels: ["skip_autoposter"],
    genre_mapper: {},
  },
  badges: { enabled: true },
  maintenance: { empty_trash: false },
  collections: { libraries: ["Movies", "TV Shows"] },
  libraries: {},
  // What the schema says each overridable leaf holds, under the same wildcard
  // the descriptions use.
  field_types: {
    "libraries.{}.operations.enabled": "boolean",
    "libraries.{}.operations.write_to_plex": "boolean",
    "libraries.{}.operations.lock_apply": "boolean",
    "libraries.{}.operations.user_rating_source": "string",
    "libraries.{}.operations.ignore_labels": "string_list",
    "libraries.{}.operations.genre_mapper": "object",
    "libraries.{}.badges.enabled": "boolean",
    "libraries.{}.maintenance.empty_trash": "boolean",
  },
  field_descriptions: {
    libraries: "Per-library overrides, keyed by Plex library name.",
    "libraries.{}.operations": "The metadata-operations settings this library uses.",
    "libraries.{}.operations.enabled":
      "This library's value for operations.enabled; unset inherits the global setting.",
    "libraries.{}.operations.write_to_plex":
      "This library's value for operations.write_to_plex; unset inherits the global setting.",
    "libraries.{}.operations.user_rating_source":
      "This library's value for operations.user_rating_source; unset inherits the global setting.",
    "libraries.{}.operations.ignore_labels":
      "This library's value for operations.ignore_labels; unset inherits the global setting.",
    "libraries.{}.operations.lock_apply":
      "This library's value for operations.lock_apply; unset inherits the global setting.",
    "libraries.{}.operations.genre_mapper":
      "This library's value for operations.genre_mapper; unset inherits the global setting.",
    "libraries.{}.badges.enabled":
      "This library's value for badges.enabled; unset inherits the global setting.",
    "libraries.{}.maintenance.empty_trash":
      "This library's value for maintenance.empty_trash; unset inherits the global setting.",
    operations: "Per-item metadata operations.",
    "operations.enabled": "Whether per-item metadata operations run at all.",
  },
};

function editorFor(document: Record<string, unknown> = {}) {
  return {
    document,
    saved: document,
    frozen: {},
    computed: [],
    live: [],
    redacted: [],
    sentinel: "",
    errors: {},
    setValue: vi.fn(),
    clear: vi.fn(),
    revert: vi.fn(),
  };
}

describe("overridableLeaves", () => {
  it("derives the whitelist from the served descriptions and nothing else", () => {
    // The section headings carry entries of their own and are not cells; the
    // global `operations.enabled` entry is not a per-library leaf at all.
    expect(overridableLeaves(CONFIG)).toEqual([
      "badges.enabled",
      "maintenance.empty_trash",
      "operations.enabled",
      "operations.genre_mapper",
      "operations.ignore_labels",
      "operations.lock_apply",
      "operations.user_rating_source",
      "operations.write_to_plex",
    ]);
  });

  it("answers nothing when the server describes no per-library leaf", () => {
    expect(overridableLeaves({ field_descriptions: {} })).toEqual([]);
    expect(overridableLeaves({})).toEqual([]);
  });
});

describe("LibraryOverridesPanel", () => {
  it("renders one row per configured library", () => {
    render(<LibraryOverridesPanel config={CONFIG} editor={editorFor()} />);
    expect(screen.getByRole("row", { name: /Movies/ })).toBeTruthy();
    expect(screen.getByRole("row", { name: /TV Shows/ })).toBeTruthy();
  });

  it("shows inherit as the empty state for every unset cell", () => {
    render(<LibraryOverridesPanel config={CONFIG} editor={editorFor()} />);
    const cell = screen.getByLabelText("libraries.Movies.operations.enabled");
    expect((cell as HTMLSelectElement).value).toBe("inherit");
    // And it says what is being inherited, so "inherits" and "set to the same
    // thing the global happens to say today" are visibly different states.
    expect(screen.getAllByText(/inherits on/i).length).toBeGreaterThan(0);
  });

  it("writes exactly one dotted path when a boolean cell is set", () => {
    const editor = editorFor();
    render(<LibraryOverridesPanel config={CONFIG} editor={editor} />);
    fireEvent.change(
      screen.getByLabelText("libraries.Movies.operations.write_to_plex"),
      { target: { value: "off" } },
    );
    expect(editor.setValue).toHaveBeenCalledTimes(1);
    expect(editor.setValue).toHaveBeenCalledWith(
      "libraries.Movies.operations.write_to_plex", false,
    );
    expect(editor.clear).not.toHaveBeenCalled();
  });

  it("writes the typed string for a text cell", () => {
    const editor = editorFor();
    render(<LibraryOverridesPanel config={CONFIG} editor={editor} />);
    fireEvent.change(
      screen.getByLabelText("libraries.Movies.operations.user_rating_source"),
      { target: { value: "tmdb" } },
    );
    expect(editor.setValue).toHaveBeenCalledWith(
      "libraries.Movies.operations.user_rating_source", "tmdb",
    );
  });

  it("choosing inherit DELETES the path and never writes the global", () => {
    /* The freezing hazard, named.
     *
     * Writing `libraries.Movies.operations.write_to_plex: true` because that
     * is what the global says today would freeze this library at today's
     * value, and the next edit to the deployed YAML would silently stop
     * reaching it. The only correct clear is the key going away -- which is
     * also why `null` is not an option: the API validates it like any other
     * value and a null-valued setting is almost always invalid.
     */
    const editor = editorFor({
      libraries: { Movies: { operations: { write_to_plex: false } } },
    });
    render(<LibraryOverridesPanel config={CONFIG} editor={editor} />);
    fireEvent.change(
      screen.getByLabelText("libraries.Movies.operations.write_to_plex"),
      { target: { value: "inherit" } },
    );
    expect(editor.clear).toHaveBeenCalledTimes(1);
    expect(editor.clear).toHaveBeenCalledWith(
      "libraries.Movies.operations.write_to_plex",
    );
    expect(editor.setValue).not.toHaveBeenCalled();
  });

  it("clearing a text cell deletes the path rather than writing an empty string", () => {
    const editor = editorFor({
      libraries: { Movies: { operations: { user_rating_source: "tmdb" } } },
    });
    render(<LibraryOverridesPanel config={CONFIG} editor={editor} />);
    fireEvent.change(
      screen.getByLabelText("libraries.Movies.operations.user_rating_source"),
      { target: { value: "" } },
    );
    expect(editor.clear).toHaveBeenCalledWith(
      "libraries.Movies.operations.user_rating_source",
    );
    expect(editor.setValue).not.toHaveBeenCalled();
  });

  it("shows a set cell's stored value, not the global one", () => {
    const editor = editorFor({
      libraries: { Movies: { badges: { enabled: false } } },
    });
    render(<LibraryOverridesPanel config={CONFIG} editor={editor} />);
    expect(
      (screen.getByLabelText("libraries.Movies.badges.enabled") as HTMLSelectElement)
        .value,
    ).toBe("off");
    // The other library is untouched and still says so.
    expect(
      (screen.getByLabelText("libraries.TV Shows.badges.enabled") as HTMLSelectElement)
        .value,
    ).toBe("inherit");
  });

  it("sets a per-library list here, entry by entry", () => {
    // The one thing the generic tree beneath this panel used to be able to do
    // -- and could only do for a list that already held a value, since it
    // chose its control from the value. An unset one is created by the first
    // entry added.
    const editor = editorFor();
    render(<LibraryOverridesPanel config={CONFIG} editor={editor} />);

    const cell = screen.getByTestId("cell-Movies-operations.ignore_labels");
    fireEvent.click(
      within(cell).getByRole("button", {
        name: "Add to libraries.Movies.operations.ignore_labels",
      }),
    );
    expect(editor.setValue).toHaveBeenCalledWith(
      "libraries.Movies.operations.ignore_labels",
      [""],
    );
  });

  it("inherit on a set list cell deletes the path", () => {
    const editor = editorFor({
      libraries: { Movies: { operations: { ignore_labels: ["Skip"] } } },
    });
    render(<LibraryOverridesPanel config={CONFIG} editor={editor} />);

    const cell = screen.getByTestId("cell-Movies-operations.ignore_labels");
    expect(
      within(cell).getByLabelText("libraries.Movies.operations.ignore_labels[0]"),
    ).toHaveValue("Skip");
    fireEvent.click(
      within(cell).getByRole("button", {
        name: "Inherit libraries.Movies.operations.ignore_labels",
      }),
    );
    expect(editor.clear).toHaveBeenCalledWith(
      "libraries.Movies.operations.ignore_labels",
    );
    expect(editor.setValue).not.toHaveBeenCalled();
  });

  it("offers no editor for a mapping leaf and says so", () => {
    const editor = editorFor();
    render(<LibraryOverridesPanel config={CONFIG} editor={editor} />);
    const cell = screen.getByTestId("cell-Movies-operations.genre_mapper");
    expect(within(cell).queryByRole("textbox")).toBeNull();
    expect(within(cell).queryByRole("combobox")).toBeNull();
    expect(screen.getByText(/mapping settings/i)).toBeTruthy();
  });

  it("takes a cell's control from the schema's kind, not from the global value", () => {
    // `operations.lock_apply` holds a boolean and its global is unset, so the
    // value says nothing about what the cell is -- it used to fall through to
    // the text branch, which is the same defect the settings page had.
    render(<LibraryOverridesPanel config={CONFIG} editor={editorFor()} />);
    const cell = screen.getByLabelText("libraries.Movies.operations.lock_apply");
    expect(cell.tagName).toBe("SELECT");
    expect((cell as HTMLSelectElement).value).toBe("inherit");
  });

  it("falls back to the global's own type when the server names no kinds", () => {
    // An older deployment serves no `field_types`. Every cell whose global
    // has a value is what it was before, so the panel still works.
    const { field_types: _unused, ...older } = CONFIG;
    render(<LibraryOverridesPanel config={older} editor={editorFor()} />);
    expect(
      screen.getByLabelText("libraries.Movies.operations.enabled").tagName,
    ).toBe("SELECT");
    const list = screen.getByTestId("cell-Movies-operations.ignore_labels");
    expect(within(list).queryByRole("textbox")).toBeNull();
  });

  it("gives a column to a library that overrides something out of collections scope", () => {
    // Per-library overrides cover badges, operations and maintenance, which
    // apply to libraries outside `collections.libraries`. Such an override is
    // stored and applied, and with the generic tree gone this table is the
    // only thing that can show it.
    const config = {
      ...CONFIG,
      libraries: { Anime: { badges: { enabled: false } } },
    };
    const editor = editorFor({
      libraries: { Anime: { badges: { enabled: false } } },
    });
    render(<LibraryOverridesPanel config={config} editor={editor} />);

    expect(screen.getByRole("columnheader", { name: "Anime" })).toBeTruthy();
    expect(
      (screen.getByLabelText("libraries.Anime.badges.enabled") as HTMLSelectElement)
        .value,
    ).toBe("off");
  });

  it("still renders for an override when no library is in collections scope", () => {
    const config = {
      ...CONFIG,
      collections: { libraries: [] },
      libraries: { Anime: { badges: { enabled: false } } },
    };
    render(<LibraryOverridesPanel config={config} editor={editorFor(config)} />);
    expect(screen.getByRole("columnheader", { name: "Anime" })).toBeTruthy();
  });

  it("states that a change here re-renders no artwork", () => {
    /* Leg 2 of the storm proof as copy. `_render_affecting` reads version and
     * skip_tba only, so a whitelist edit reports "no re-renders" -- and the
     * panel says so outright rather than showing an empty impact block the
     * operator would compare against a real count. */
    render(<LibraryOverridesPanel config={CONFIG} editor={editorFor()} />);
    expect(screen.getByText(/changes no rendered artwork/i)).toBeTruthy();
  });

  it("renders nothing at all when no library is configured and none overrides", () => {
    const { container } = render(
      <LibraryOverridesPanel
        config={{ ...CONFIG, collections: { libraries: [] }, libraries: {} }}
        editor={editorFor()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });
});
