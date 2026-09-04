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

import {
  COLLECTION_DEFINITIONS,
  DefinitionEditor,
  draftFrom,
  entryFromDraft,
  hostOnly,
  PLAYLIST_DEFINITIONS,
} from "./DefinitionEditor";

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
      kind={COLLECTION_DEFINITIONS}
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
    expect(
      entryFromDraft(entry, draftFrom(entry, LIBRARIES), roster, COLLECTION_DEFINITIONS),
    ).toEqual(entry);
  });

  it("reduces a URL to its host and says so when it cannot", () => {
    expect(hostOnly("https://hooks.example/T0K3N/path")).toBe("hooks.example");
    expect(hostOnly("not a url")).toBe("(unreadable URL)");
  });
});

/** A stored PLAYLIST entry. `filters` and `sort_title` are refused by the
 * schema on a playlist, so they cannot appear here; `schedule` can, and it is
 * the field this form must carry through untouched. */
const PLAYLIST_ENTRY = {
  title: "Star Wars (Timeline Order)",
  builder: "imdb_list",
  params: { list: "ls501373412" },
  summary: "In-universe order.",
  builder_level: "episode",
  schedule: { every_n_runs: 4 },
};

function renderPlaylistEditor(
  entry: Record<string, unknown>,
  onSave = vi.fn(),
) {
  render(
    <DefinitionEditor
      entry={entry}
      libraries={LIBRARIES}
      descriptions={{}}
      busy={false}
      kind={PLAYLIST_DEFINITIONS}
      onSave={onSave}
      onCancel={vi.fn()}
    />,
  );
  return onSave;
}

describe("the playlist kind", () => {
  it("offers only the fields a playlist has, and none a collection has", () => {
    renderPlaylistEditor(PLAYLIST_ENTRY);

    expect(screen.getByLabelText("Title")).toBeInTheDocument();
    expect(screen.getByLabelText("Summary")).toBeInTheDocument();
    expect(screen.getByLabelText("Sync mode")).toBeInTheDocument();
    expect(screen.getByLabelText("Builder level")).toBeInTheDocument();
    expect(screen.getByLabelText("Limit")).toBeInTheDocument();
    // Every one of these is in `_REFUSED_PLAYLIST_FIELDS`: offering the
    // control would offer an edit config load is bound to reject.
    expect(screen.queryByLabelText("Sort")).toBeNull();
    expect(screen.queryByLabelText("Sort title")).toBeNull();
    expect(screen.queryByLabelText("Collection mode")).toBeNull();
    expect(screen.queryByLabelText("Label sync")).toBeNull();
  });

  it("writes builder_level, and drops the key again at its default", () => {
    const onSave = renderPlaylistEditor(PLAYLIST_ENTRY);

    fireEvent.change(screen.getByLabelText("Builder level"), {
      target: { value: "item" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Absence is the overrides document's revert, and `item` IS the schema's
    // default -- so writing it explicitly would pin a value the operator did
    // not choose.
    expect(onSave).toHaveBeenCalledTimes(1);
    expect("builder_level" in onSave.mock.calls[0][0]).toBe(false);
  });

  it("carries an unedited key through, so a schedule survives an edit", () => {
    const onSave = renderPlaylistEditor(PLAYLIST_ENTRY);

    fireEvent.change(screen.getByLabelText("Summary"), {
      target: { value: "Release order." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave.mock.calls[0][0]).toMatchObject({
      title: "Star Wars (Timeline Order)",
      builder: "imdb_list",
      params: { list: "ls501373412" },
      summary: "Release order.",
      builder_level: "episode",
      schedule: { every_n_runs: 4 },
    });
  });
});
