/** The Custom collections panel: the fourth writer of the overrides document,
 * and the one whose whole value is what it REFUSES to write.
 *
 * Two refusals are pinned here, and they are not the same refusal.
 *
 *   - The HARD GUARD: while any listed definition comes from the mounted
 *     config file, create is refused outright. An overrides list REPLACES the
 *     file's wholesale, so the first save here would silently stop every file
 *     row from being built, and the freezing ban forbids the alternative
 *     (copying the file's rows in would pin today's YAML values forever).
 *   - The freezing hazard proper: nothing this panel writes is ever built
 *     from the listing. The listing is a lossy seven-field projection; the
 *     write is seeded from `documentFromConfig` — the stored document's own
 *     entries — so a stored definition's `summary`, `limit`, `schedule` and
 *     the rest survive every rewrite.
 *
 * Everything else is the sibling panels' contract: whole-list writes,
 * key-removal as the revert, unrelated overrides preserved, 422s rendered
 * against the path they name, re-read after save.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { STALE_SAVE_NOTE } from "../api/overrides";
import { CustomCollectionsPanel } from "./CustomCollectionsPanel";

const LIBRARIES = ["Movies", "TV Shows"];

const FILE_ROW = {
  title: "Hand Picked",
  builder: "plex_id",
  params: { ids: ["12345"] },
  libraries: ["Movies"],
  sort: "custom",
  sync_mode: "sync",
  provenance: "file",
};

const OVERRIDE_ROWS = [
  {
    title: "Star Wars",
    builder: "tmdb_collection",
    params: { id: 10 },
    libraries: null,
    sort: "custom",
    sync_mode: "sync",
    provenance: "override",
  },
  {
    title: "Weekly Watched",
    builder: "mdblist_list",
    params: { list: "someone/weekly" },
    libraries: ["Movies"],
    sort: "custom",
    sync_mode: "sync",
    provenance: "override",
  },
];

/** The stored entries behind OVERRIDE_ROWS, as the served config carries
 * them: `overridden_paths` names the list-as-leaf path and the served value
 * IS the stored one (an override wins the merge), so `documentFromConfig`
 * seeds exactly this array.
 *
 * `summary` and `limit` are the point of the second entry: the listing does
 * not project them, so a write built from the listing would drop them. */
const STORED_ENTRIES = [
  { title: "Star Wars", builder: "tmdb_collection", params: { id: 10 } },
  {
    title: "Weekly Watched",
    builder: "mdblist_list",
    params: { list: "someone/weekly" },
    libraries: ["Movies"],
    summary: "What the household watched this week.",
    limit: 25,
  },
];

function listing(definitions: unknown[] = []) {
  return { libraries: LIBRARIES, definitions };
}

/** The config GET, whose only job here is seeding the document the panel
 * writes. The unrelated `plex.url` override is the point of the fixture: a
 * save about definitions must not drop it. */
function config(overrides: Record<string, unknown> = {}) {
  return {
    version: "cfg-1",
    plex: { url: "http://plex:32400" },
    collections: { enabled: true },
    overridden_paths: ["plex.url"],
    frozen_paths: {},
    redacted_paths: [],
    keep_sentinel: "***KEEP***",
    ...overrides,
  };
}

/** A config already storing a definitions override, so Remove has stored
 * entries to subtract from. */
function overriddenConfig(entries: unknown[] = STORED_ENTRIES) {
  return config({
    collections: { enabled: true, definitions: entries },
    overridden_paths: ["plex.url", "collections.definitions"],
  });
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface StubOptions {
  definitions?: unknown;
  config?: unknown;
  parse?: (init?: RequestInit) => Response;
  preview?: (init?: RequestInit) => Response;
  save?: (init?: RequestInit) => Response;
}

function stubFetch(options: StubOptions = {}) {
  const puts: RequestInit[] = [];
  const previews: RequestInit[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/collections/definitions") {
      return json(options.definitions ?? listing());
    }
    if (path === "/api/config") return json(options.config ?? config());
    if (path === "/api/collections/parse-source") {
      if (options.parse) return options.parse(init);
      return json({
        builder: "imdb_list",
        params: { list: "ls055350410" },
        display_note: "an IMDb list, in list order",
      });
    }
    if (path === "/api/config/preview") {
      previews.push(init ?? {});
      if (options.preview) return options.preview(init);
      return json({
        version_before: "cfg-1",
        version_after: "cfg-2",
        restart_required: [],
        inert: [],
        impact: null,
      });
    }
    if (path === "/api/config/overrides") {
      puts.push(init ?? {});
      if (options.save) return options.save(init);
      return json({
        version_before: "cfg-1",
        version_after: "cfg-2",
        restart_required: [],
      });
    }
    throw new Error(`unexpected fetch: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, puts, previews };
}

/** The document a PUT (or preview POST) carried, parsed. */
function sentDocument(requests: RequestInit[], index = 0): Record<string, any> {
  const body = requests[index]?.body;
  expect(typeof body).toBe("string");
  return JSON.parse(body as string).document;
}

async function renderPanel(options: StubOptions = {}) {
  const stub = stubFetch(options);
  render(<CustomCollectionsPanel />);
  await screen.findByText("Create from a list URL");
  return stub;
}

/** Fill the form and resolve the URL through the (stubbed) parse endpoint. */
async function fillAndParse(
  title = "Space Operas",
  url = "https://www.imdb.com/list/ls055350410/",
) {
  fireEvent.change(screen.getByLabelText("Collection title"), {
    target: { value: title },
  });
  fireEvent.change(screen.getByLabelText("Source URL"), { target: { value: url } });
  fireEvent.click(screen.getByRole("button", { name: "Read URL" }));
  await screen.findByText("imdb_list");
}

beforeEach(() => {
  setToken(null);
});

describe("the custom collections panel", () => {
  it("lists definitions with provenance, offering Remove only on override rows", async () => {
    await renderPanel({
      definitions: listing([FILE_ROW, ...OVERRIDE_ROWS]),
      config: overriddenConfig(),
    });

    const fileRow = within(screen.getByText("Hand Picked").closest("tr")!);
    expect(fileRow.getByText("config file")).toBeInTheDocument();
    expect(fileRow.queryByRole("button", { name: /Remove/ })).toBeNull();

    expect(screen.getByRole("button", { name: "Remove Star Wars" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Remove Weekly Watched" }),
    ).toBeEnabled();
    expect(screen.getAllByText("override")).toHaveLength(2);
  });

  it("shows the wholesale-replace warning while file rows exist", async () => {
    await renderPanel({ definitions: listing([FILE_ROW]) });

    expect(screen.getByText(/do not merge/)).toBeInTheDocument();
  });

  it("hides the warning when every row is an override", async () => {
    await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    expect(screen.queryByText(/do not merge/)).toBeNull();
  });

  it("refuses create outright while file rows exist, naming both ways forward", async () => {
    // THE HARD GUARD (facts Addendum). A create here would store an overrides
    // list that REPLACES the file's, so "Hand Picked" would quietly stop being
    // built — and copying it into the write instead is the freezing hazard.
    // Neither is offered: the create is refused, and nothing is written.
    const { puts, previews } = await renderPanel({
      definitions: listing([FILE_ROW]),
    });

    await fillAndParse();

    const create = screen.getByRole("button", { name: "Create" });
    expect(create).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();
    fireEvent.click(create);
    expect(puts).toHaveLength(0);
    expect(previews).toHaveLength(0);

    // The refusal has to say what to do instead, and the button has to point
    // at the sentence that says it.
    const guard = screen.getByText(/do not merge/);
    expect(guard).toHaveTextContent("keep managing definitions in the config file");
    expect(guard).toHaveTextContent("empty the file's definitions list");
    expect(create).toHaveAttribute("aria-describedby", guard.id);
    expect(guard.id).not.toBe("");
  });

  it("a file-defined definition never enters the overrides write", async () => {
    // The freezing hazard from the other side: the write is
    // seeded from the STORED document, never from the listing, so a row the
    // listing shows but the overrides document does not hold cannot reach the
    // payload. Driven through Remove (the guard does not disable it) with a
    // genuinely mixed listing, so "Hand Picked" is on screen and provenance
    // "file" — the only way it could be absent from the payload is because
    // the write never reads the listing at all. The `toEqual` also pins
    // `overrideOrdinal`'s counting: a raw index (2) would filter nothing and
    // leave both stored entries, instead of dropping "Weekly Watched" at its
    // true override-only ordinal (1).
    const { puts } = await renderPanel({
      definitions: listing([FILE_ROW, ...OVERRIDE_ROWS]),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Weekly Watched" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    expect(document.collections.definitions).toEqual([STORED_ENTRIES[0]]);
    expect(JSON.stringify(document)).not.toContain("Hand Picked");
  });

  it("writes the stored entries, not the listing's seven-field projection", async () => {
    // The listing is display-only. `summary` and `limit` are stored fields it
    // does not project; a write rebuilt from the listing would drop them from
    // an untouched neighbour on every save.
    const { puts } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const written = sentDocument(puts).collections.definitions;
    expect(written[1]).toEqual(STORED_ENTRIES[1]);
    expect(written[1].summary).toBe("What the household watched this week.");
    expect(written[1].limit).toBe(25);
  });

  it("writes only the new entry on a first create, keeping unrelated overrides", async () => {
    const { puts } = await renderPanel();

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    // All library boxes are checked, so the `libraries` key is omitted -- the
    // definition's own "every configured library" default.
    expect(document.collections.definitions).toEqual([
      { title: "Space Operas", builder: "imdb_list", params: { list: "ls055350410" } },
    ]);
    // The unrelated override the config reported is still in the document.
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("narrows the entry to the checked libraries", async () => {
    const { puts } = await renderPanel();

    await fillAndParse();
    fireEvent.click(screen.getByRole("checkbox", { name: "TV Shows" }));
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(sentDocument(puts).collections.definitions).toEqual([
      {
        title: "Space Operas",
        builder: "imdb_list",
        params: { list: "ls055350410" },
        libraries: ["Movies"],
      },
    ]);
  });

  it("appends to the stored override list on create", async () => {
    const { puts } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(sentDocument(puts).collections.definitions).toEqual([
      ...STORED_ENTRIES,
      { title: "Space Operas", builder: "imdb_list", params: { list: "ls055350410" } },
    ]);
  });

  it("removes one override row by its stored position", async () => {
    const { puts } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Star Wars" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(sentDocument(puts).collections.definitions).toEqual([STORED_ENTRIES[1]]);
  });

  it("removing the last override drops the key — the overrides revert", async () => {
    const { puts } = await renderPanel({
      definitions: listing([OVERRIDE_ROWS[0]]),
      config: overriddenConfig([STORED_ENTRIES[0]]),
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Star Wars" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    // The revert IS the absence: `[]` would keep shadowing whatever the file
    // lists; the key going away hands the decision back to the file.
    expect(document.collections).toBeUndefined();
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("Check posts to the preview endpoint and stores nothing", async () => {
    const { puts, previews } = await renderPanel();

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Check" }));

    await screen.findByText(/Nothing was stored/);
    expect(previews).toHaveLength(1);
    expect(puts).toHaveLength(0);
    expect(sentDocument(previews).collections.definitions).toEqual([
      { title: "Space Operas", builder: "imdb_list", params: { list: "ls055350410" } },
    ]);
  });

  it("renders the parse refusal under the URL field", async () => {
    await renderPanel({
      parse: () =>
        json(
          {
            detail:
              "trakt.tv is not a supported source: no trakt builder is shipped",
          },
          422,
        ),
    });

    fireEvent.change(screen.getByLabelText("Source URL"), {
      target: { value: "https://trakt.tv/users/x/lists/y" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Read URL" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("no trakt builder is shipped");
  });

  it("renders a parse 422 that arrives as FastAPI's list of entries", async () => {
    // The endpoint answers 422 in two shapes: its own refusal is a string
    // `detail`, and a body the request validator rejects is a list of
    // `{loc, msg}` objects. Rendering the second one raw is "[object Object]".
    await renderPanel({
      parse: () =>
        json(
          {
            detail: [
              {
                loc: ["body", "url"],
                msg: "Input should be a valid string",
                type: "string_type",
              },
            ],
          },
          422,
        ),
    });

    fireEvent.change(screen.getByLabelText("Source URL"), {
      target: { value: "https://www.imdb.com/list/ls1/" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Read URL" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Input should be a valid string");
    expect(alert).not.toHaveTextContent("object Object");
  });

  it("renders a save 422 against the path it names", async () => {
    await renderPanel({
      save: () =>
        json(
          {
            detail: [
              {
                path: "collections",
                message:
                  "collection definition 'Space Operas' has the same title as a " +
                  "built-in collection this service already builds",
              },
            ],
          },
          422,
        ),
    });

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    expect(await screen.findByText(/has the same title/)).toBeInTheDocument();
    expect(screen.getByText("collections")).toBeInTheDocument();
  });

  it("clears the form and re-reads after a create, reporting versions", async () => {
    const { fetchMock, puts } = await renderPanel();

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(puts).toHaveLength(1));

    // What is stored is the server's answer, not this component's memory.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([path]) => path === "/api/collections/definitions"),
      ).toHaveLength(2),
    );
    expect(screen.getByLabelText("Collection title")).toHaveValue("");
    expect(screen.getByLabelText("Source URL")).toHaveValue("");
    const saved = await screen.findByText(/Saved\./);
    expect(saved).toHaveTextContent("cfg-1");
    expect(saved).toHaveTextContent("cfg-2");
  });

  it("disables Check and Create until parsed, titled, and scoped to at least one library", async () => {
    await renderPanel();

    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Check" })).toBeDisabled();

    await fillAndParse();
    expect(screen.getByRole("button", { name: "Create" })).toBeEnabled();

    // Zero libraries would write `libraries: []` -- "no library at all",
    // which the schema allows and no operator means.
    fireEvent.click(screen.getByRole("checkbox", { name: "Movies" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "TV Shows" }));
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });

  it("offers Edit on override rows and never on file rows", async () => {
    await renderPanel({
      definitions: listing([FILE_ROW]),
      config: config(),
    });

    // Provenance is uniform: a listing with a file row has no override rows
    // at all, so the guard is structural -- there is nothing to edit.
    expect(screen.queryByRole("button", { name: "Edit Hand Picked" })).toBeNull();
  });

  it("seeds the edit form from the STORED entry, never from the listing", async () => {
    await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Weekly Watched" }));

    // `summary` is in the stored entry and NOT in the seven-field listing --
    // a form built from the listing would show it empty and then save the
    // blank over the operator's text.
    expect(screen.getByLabelText("Summary")).toHaveValue(
      "What the household watched this week.",
    );
    expect(screen.getByLabelText("Limit")).toHaveValue(25);
  });

  it("writes the whole list back with only the edited entry changed", async () => {
    const { puts } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Weekly Watched" }));
    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/^Saved\./);

    const document = sentDocument(puts);
    // The negative assertion: the sibling is byte-identical.
    expect(document.collections.definitions[0]).toEqual(STORED_ENTRIES[0]);
    expect(document.collections.definitions[1]).toEqual({
      ...STORED_ENTRIES[1],
      limit: 10,
    });
    expect(document.collections.definitions).toHaveLength(2);
    // And an override about something else is still there.
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("renders a 422 from the edit against the path the server named", async () => {
    await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
      save: () =>
        json(
          {
            detail: [
              {
                path: "collections.definitions",
                message: "the mounted config file lists collections.definitions",
              },
            ],
          },
          422,
        ),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Star Wars" }));
    fireEvent.change(screen.getByLabelText("Limit"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("The server rejected this change.")).toBeInTheDocument();
    // The path scoped to the errors list: the panel's own intro sentence also
    // carries the literal text "collections.definitions" in a span of its own.
    expect(
      screen.getByText("collections.definitions", { selector: ".custom-errors .mono" }),
    ).toBeInTheDocument();
  });

  it("closes the form and re-reads after a save", async () => {
    const { fetchMock } = await renderPanel({
      definitions: listing(OVERRIDE_ROWS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Star Wars" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/^Saved\./);

    expect(screen.queryByText("Edit definition")).toBeNull();
    // Two definitions reads and two config reads: the mount, then the re-read.
    const reads = fetchMock.mock.calls.filter(
      ([path]) => path === "/api/collections/definitions",
    );
    expect(reads).toHaveLength(2);
  });

  it("no longer claims there is no edit", async () => {
    await renderPanel({ definitions: listing(OVERRIDE_ROWS), config: overriddenConfig() });

    expect(screen.queryByText(/There is no edit/)).toBeNull();
  });
});

describe("CustomCollectionsPanel stale-save recovery", () => {
  /** This file's parse-then-create idiom, matching the neighbouring create
   * tests -- there is no shared helper for it. */
  async function createDefinition(puts: RequestInit[]) {
    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(puts).toHaveLength(1));
  }

  it("sends the revision it seeded from", async () => {
    const { puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
    });
    await createDefinition(puts);

    expect(JSON.parse(String(puts[0].body)).expected_revision).toBe("rev-1");
  });

  it("tells the operator and re-reads when the server refuses a stale save", async () => {
    const { fetchMock, puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
      save: () =>
        json(
          {
            message: "these settings changed somewhere else",
            current_revision: "rev-9",
            changed_paths: ["collections.definitions"],
          },
          409,
        ),
    });
    await createDefinition(puts);

    await screen.findByText(STALE_SAVE_NOTE);
    expect(puts).toHaveLength(1);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([path]) => path === "/api/config"),
      ).toHaveLength(2),
    );
  });

  it("never says 'Saved' when the refused save's re-read also fails", async () => {
    // The doubly-degraded path: a 409 saved nothing, and the re-read that
    // would have told the truth about what IS stored failed too. Saying
    // "Saved, but..." here would be the incident's own lie in miniature.
    let configReads = 0;
    const fetchMock = vi.fn(async (path: string) => {
      if (path === "/api/collections/definitions") return json(listing());
      if (path === "/api/config") {
        configReads += 1;
        if (configReads > 1) return json({ detail: "the database is unreachable" }, 500);
        return json({ ...config(), overrides_revision: "rev-1" });
      }
      if (path === "/api/collections/parse-source") {
        return json({
          builder: "imdb_list",
          params: { list: "ls055350410" },
          display_note: "an IMDb list, in list order",
        });
      }
      if (path === "/api/config/overrides") {
        return json({ message: "changed elsewhere", changed_paths: [] }, 409);
      }
      throw new Error(`unexpected fetch: ${path}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<CustomCollectionsPanel />);
    await screen.findByText("Create from a list URL");

    await fillAndParse();
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    const note = await screen.findByText(/could not be re-read/);
    expect(note).toHaveTextContent(/^Nothing was saved, and/);
    expect(note).not.toHaveTextContent(/Saved, but/);
  });
});
