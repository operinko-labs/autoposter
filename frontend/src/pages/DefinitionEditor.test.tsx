/** The definitions editor: row 138's literal scope, and the one rule that
 * makes it safe.
 *
 * The rule is that an edited entry is `{...storedEntry, ...curatedEdits}` --
 * the eleven curated fields are the only keys the form may add, change or
 * delete, and every other key of the stored entry rides through untouched.
 * The seven-field listing is never a source; the stored overrides array is.
 *
 * Two more things are pinned here. `libraries` has its own "every configured
 * library" control rather than being inferred from all-boxes-checked, because
 * an explicit list of today's names and the absent key are DIFFERENT requests.
 * And `changes_webhook` is rendered host-only: it may embed a token, so it
 * gets the discipline `notifications.url` gets. */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DefinitionEditor, draftFrom, entryFromDraft, hostOnly } from "./DefinitionEditor";

const LIBRARIES = ["Movies", "TV Shows"];

/** A stored entry carrying five fields the form does not reach. */
const RICH_ENTRY = {
  title: "Weekly Watched",
  builder: "mdblist_list",
  params: { list: "someone/weekly" },
  summary: "What the household watched.",
  schedule: { every_n_runs: 3 },
  filters: { "year.gte": 2000 },
  tmdb_summary: 12345,
  changes_webhook: "https://hooks.example/T0K3N/path",
};

const DESCRIPTIONS = {
  "collections.definitions[].limit":
    "A cap on the collection's member count, applied after resolution.",
};

function renderEditor(entry: Record<string, unknown>, onSave = vi.fn()) {
  render(
    <DefinitionEditor
      entry={entry}
      libraries={LIBRARIES}
      descriptions={DESCRIPTIONS}
      busy={false}
      onSave={onSave}
      onCancel={vi.fn()}
    />,
  );
  return onSave;
}

describe("the definition editor", () => {
  it("round-trips an untouched entry byte-for-byte", () => {
    const onSave = renderEditor(RICH_ENTRY);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith(RICH_ENTRY);
  });

  it("preserves every field the form does not reach", () => {
    const onSave = renderEditor(RICH_ENTRY);

    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({ ...RICH_ENTRY, limit: 25 });
  });

  it("writes a key only when its value differs from the schema default", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
    });

    // Typing the default back into a field that never had the key must not
    // add it: an explicit "custom" and an absent `sort` are the same order.
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "custom" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
    });
  });

  it("clears an optional field by dropping its key, never by writing null", () => {
    const onSave = renderEditor({ ...RICH_ENTRY, limit: 25 });

    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith(RICH_ENTRY);
    expect(Object.keys(onSave.mock.calls[0][0])).not.toContain("limit");
  });

  it("keeps an explicit library list explicit when the edit is about something else", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      libraries: ["Movies", "TV Shows"],
    });

    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // All boxes are checked -- but the stored entry SAID these two names, and
    // dropping the key would silently re-scope it to whatever
    // collections.libraries lists next month.
    expect(onSave).toHaveBeenCalledWith({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      libraries: ["Movies", "TV Shows"],
      limit: 5,
    });
  });

  it("drops the libraries key when the every-library control is turned on", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      libraries: ["Movies"],
    });

    fireEvent.click(screen.getByLabelText("Every configured library"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledWith({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
    });
  });

  it("offers a library the config no longer lists rather than dropping it", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      libraries: ["Movies", "Retired Library"],
    });

    expect(screen.getByLabelText("Retired Library")).toBeChecked();

    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave.mock.calls[0][0].libraries).toEqual(["Movies", "Retired Library"]);
  });

  it("refuses to save an empty scope, naming why", () => {
    renderEditor({ title: "Star Wars", builder: "tmdb_collection", params: { id: 10 } });

    fireEvent.click(screen.getByLabelText("Every configured library"));
    fireEvent.click(screen.getByLabelText("Movies"));
    fireEvent.click(screen.getByLabelText("TV Shows"));

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByText(/no library at all/)).toBeInTheDocument();
  });

  it("renders the change webhook host-only and never its path", () => {
    renderEditor(RICH_ENTRY);

    expect(screen.getByText("hooks.example")).toBeInTheDocument();
    expect(screen.queryByText(/T0K3N/)).toBeNull();
  });

  it("shows the builder and params without offering to edit them", () => {
    renderEditor(RICH_ENTRY);

    expect(screen.getByText("mdblist_list")).toBeInTheDocument();
    expect(screen.getByText('{"list":"someone/weekly"}')).toBeInTheDocument();
    expect(screen.queryByLabelText("Builder")).toBeNull();
    expect(screen.queryByLabelText("Params")).toBeNull();
  });

  it("hangs the schema's own description on the field as hover text", () => {
    renderEditor(RICH_ENTRY);

    expect(screen.getByText("Limit").closest("label")).toHaveAttribute(
      "title",
      DESCRIPTIONS["collections.definitions[].limit"],
    );
  });

  it("edits labels as a list, so a label containing a comma survives", () => {
    const onSave = renderEditor({
      title: "Star Wars",
      builder: "tmdb_collection",
      params: { id: 10 },
      labels: ["Space, Opera"],
    });

    fireEvent.click(screen.getByRole("button", { name: "Add label" }));
    fireEvent.change(screen.getByLabelText("labels[1]"), { target: { value: "Sci-Fi" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave.mock.calls[0][0].labels).toEqual(["Space, Opera", "Sci-Fi"]);
  });
});

describe("the editor's pure halves", () => {
  it("seeds the every-library control from whether the key was there", () => {
    expect(draftFrom({ title: "A", builder: "b", params: {} }, LIBRARIES).everyLibrary)
      .toBe(true);
    expect(
      draftFrom({ title: "A", builder: "b", params: {}, libraries: ["Movies"] }, LIBRARIES)
        .everyLibrary,
    ).toBe(false);
  });

  it("never invents a key the draft did not set", () => {
    const entry = { title: "A", builder: "b", params: {} };
    const roster = LIBRARIES;
    expect(entryFromDraft(entry, draftFrom(entry, LIBRARIES), roster)).toEqual(entry);
  });

  it("reduces a URL to its host and says so when it cannot", () => {
    expect(hostOnly("https://hooks.example/T0K3N/path")).toBe("hooks.example");
    expect(hostOnly("not a url")).toBe("(unreadable URL)");
  });
});
