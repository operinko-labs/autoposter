/** The playlists panel: the fifth writer of the overrides document.
 *
 * `CustomCollectionsPanel.test.tsx`'s contract, one section along, plus the
 * one thing that section has and collections do not — preset rows. A preset is
 * data the config expands, stored in no document, so it renders with a badge
 * and NO controls: there is nothing for Remove to subtract from and nothing
 * for Edit to splice into. Switching one off is removing its key from
 * `playlists.presets`, which the settings page already edits.
 *
 * Everything else is the sibling panels' law: whole-list writes seeded from
 * the STORED document and never from the listing (the freezing hazard
 * `api/overrides.ts` opens with — and it bites harder here, because a playlist
 * row carries a `schedule` this panel's form never shows), key-removal as the
 * revert, unrelated overrides preserved, the hard file-provenance guard, and a
 * 409 that re-reads instead of retrying.
 *
 * No storage stub: this file touches neither `localStorage` nor
 * `sessionStorage`, which is pinned mechanically rather than
 * trusting this sentence. */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { STALE_SAVE_NOTE } from "../api/overrides";
import { PlaylistsPanel } from "./PlaylistsPanel";

const LIBRARIES = ["Movies", "TV Shows"];

const PRESET_ROW = {
  title: "Star Wars (Timeline Order)",
  builder: "imdb_list",
  params: { list: "ls501373412" },
  libraries: null,
  summary: null,
  limit: null,
  schedule: null,
  sync_mode: "sync",
  builder_level: "item",
  provenance: "preset",
  preset_key: "star_wars_timeline",
};

const FILE_ROW = {
  title: "Hand Picked",
  builder: "imdb_list",
  params: { list: "ls055350410" },
  libraries: ["Movies"],
  summary: null,
  limit: null,
  schedule: null,
  sync_mode: "sync",
  builder_level: "item",
  provenance: "file",
  preset_key: null,
};

const OVERRIDE_ROW = {
  title: "Weekly Watched",
  builder: "mdblist_list",
  params: { list: "someone/weekly" },
  libraries: ["Movies"],
  summary: "What the household watched.",
  limit: 25,
  // The LISTING's shape, so both of `ScheduleGate`'s fields: the handler
  // dumps the whole model (`model_dump(mode="json")`), which is
  // why `months` is here and is not in `STORED_ENTRIES` below. That gap is
  // the point of the edit test: the form shows neither field, so a write
  // rebuilt from this row would corrupt the stored one.
  schedule: { every_n_runs: 3, months: null },
  sync_mode: "sync",
  builder_level: "item",
  provenance: "override",
  preset_key: null,
};

/** The stored entry behind OVERRIDE_ROW. `schedule` is the point: the form
 * never shows it, so a write built from the listing rather than from here
 * would drop it. */
const STORED_ENTRIES = [
  {
    title: "Weekly Watched",
    builder: "mdblist_list",
    params: { list: "someone/weekly" },
    libraries: ["Movies"],
    summary: "What the household watched.",
    limit: 25,
    schedule: { every_n_runs: 3 },
  },
];

function listing(definitions: unknown[] = [], conflicts: unknown[] = []) {
  return { libraries: LIBRARIES, definitions, preset_conflicts: conflicts };
}

function config(overrides: Record<string, unknown> = {}) {
  return {
    version: "cfg-1",
    plex: { url: "http://plex:32400" },
    playlists: { enabled: true },
    overridden_paths: ["plex.url"],
    frozen_paths: {},
    redacted_paths: [],
    keep_sentinel: "***KEEP***",
    ...overrides,
  };
}

function overriddenConfig(entries: unknown[] = STORED_ENTRIES) {
  return config({
    playlists: { enabled: true, definitions: entries },
    overridden_paths: ["plex.url", "playlists.definitions"],
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
  save?: (init?: RequestInit) => Response;
}

function stubFetch(options: StubOptions = {}) {
  const puts: RequestInit[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/playlists/definitions") {
      return json(options.definitions ?? listing());
    }
    if (path === "/api/config") return json(options.config ?? config());
    if (path === "/api/collections/parse-source") {
      return json({
        builder: "imdb_list",
        params: { list: "ls055350410" },
        display_note: "an IMDb list, in list order",
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
  return { fetchMock, puts };
}

function sentDocument(requests: RequestInit[], index = 0): Record<string, any> {
  const body = requests[index]?.body;
  expect(typeof body).toBe("string");
  return JSON.parse(body as string).document;
}

async function renderPanel(options: StubOptions = {}) {
  const stub = stubFetch(options);
  render(<PlaylistsPanel />);
  await screen.findByText("Create from a list URL");
  return stub;
}

async function fillAndParse(title = "Space Operas") {
  fireEvent.change(screen.getByLabelText("Playlist title"), {
    target: { value: title },
  });
  fireEvent.change(screen.getByLabelText("Source URL"), {
    target: { value: "https://www.imdb.com/list/ls055350410/" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Read URL" }));
  // Scoped to the parsed note, NOT `findByText("imdb_list")`. `findBy*` uses
  // `getBy*` semantics and throws on more than one match, and once the parse
  // resolves `imdb_list` is on screen twice whenever a listing row uses that
  // builder — once in the table's Builder column, once in this note. Which
  // one wins would be a microtask race, so this waits on the region by name
  // and reads the builder inside it.
  const note = await screen.findByRole("status", { name: "Parsed source" });
  expect(within(note).getByText("imdb_list")).toBeInTheDocument();
}

beforeEach(() => {
  setToken(null);
});

describe("the playlists panel", () => {
  it("offers Edit and Remove only on override rows", async () => {
    // A preset row and an override row, and NOT a file row beside them: the
    // handler sets one provenance for every non-preset row at once
    // (`provenance = "override" if overridden else "file"`,
    // `api/playlists.py`), so a listing mixing "file" and "override" is a
    // response the server cannot produce and a stub nobody should read as the
    // contract. The file row's missing controls are pinned in the
    // file-provenance test below, on a listing the server CAN emit.
    await renderPanel({
      definitions: listing([PRESET_ROW, OVERRIDE_ROW]),
      config: overriddenConfig(),
    });

    const preset = within((await screen.findByText("Star Wars (Timeline Order)")).closest("tr")!);
    expect(preset.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(preset.queryByRole("button", { name: /Remove/ })).toBeNull();

    const override = within(screen.getByText("Weekly Watched").closest("tr")!);
    expect(override.getByRole("button", { name: "Edit Weekly Watched" })).toBeInTheDocument();
    expect(override.getByRole("button", { name: "Remove Weekly Watched" })).toBeInTheDocument();
  });

  it("names the preset key on a preset row", async () => {
    await renderPanel({ definitions: listing([PRESET_ROW]) });

    const row = within(screen.getByText("Star Wars (Timeline Order)").closest("tr")!);
    expect(row.getByText("preset")).toBeInTheDocument();
    expect(row.getByText("star_wars_timeline")).toBeInTheDocument();
  });

  it("reports a preset an operator definition displaced, by key and title", async () => {
    await renderPanel({
      definitions: listing(
        [FILE_ROW],
        [{ key: "mcu_timeline", title: "Marvel Cinematic Universe (Timeline Order)" }],
      ),
    });

    const note = await screen.findByRole("status", { name: "Displaced presets" });
    expect(note).toHaveTextContent("mcu_timeline");
    expect(note).toHaveTextContent("Marvel Cinematic Universe (Timeline Order)");
  });

  it("writes the whole definitions list and leaves unrelated overrides alone", async () => {
    const { puts } = await renderPanel({
      definitions: listing([OVERRIDE_ROW]),
      config: overriddenConfig(),
    });
    await fillAndParse();

    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(puts).toHaveLength(1));

    const document = sentDocument(puts);
    expect(document.plex.url).toBe("http://plex:32400");
    expect(document.playlists.definitions).toEqual([
      ...STORED_ENTRIES,
      {
        title: "Space Operas",
        builder: "imdb_list",
        params: { list: "ls055350410" },
      },
    ]);
  });

  it("refuses to create while any row comes from the config file", async () => {
    // The default `config()` carries no `playlists.definitions` override, so
    // an all-"file" listing is exactly what the handler would answer here.
    await renderPanel({ definitions: listing([FILE_ROW]) });
    await fillAndParse();

    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    expect(screen.getByText(/would quietly stop being built/)).toBeInTheDocument();
    // And the row itself offers nothing: a file row is not this panel's to
    // rewrite, which is the same guard the disabled Create button is.
    const file = within(screen.getByText("Hand Picked").closest("tr")!);
    expect(file.queryByRole("button", { name: /Edit/ })).toBeNull();
    expect(file.queryByRole("button", { name: /Remove/ })).toBeNull();
  });

  it("drops the key rather than writing an empty list on the last remove", async () => {
    const { puts } = await renderPanel({
      definitions: listing([OVERRIDE_ROW]),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Weekly Watched" }));
    await waitFor(() => expect(puts).toHaveLength(1));

    const document = sentDocument(puts);
    expect("definitions" in (document.playlists ?? {})).toBe(false);
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("edits from the stored entry, so a schedule the form never shows survives", async () => {
    const { puts } = await renderPanel({
      definitions: listing([OVERRIDE_ROW]),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Edit Weekly Watched" }));
    fireEvent.change(await screen.findByLabelText("Summary"), {
      target: { value: "Last week's viewing." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts).toHaveLength(1));

    expect(sentDocument(puts).playlists.definitions).toEqual([
      {
        title: "Weekly Watched",
        builder: "mdblist_list",
        params: { list: "someone/weekly" },
        libraries: ["Movies"],
        summary: "Last week's viewing.",
        limit: 25,
        schedule: { every_n_runs: 3 },
      },
    ]);
  });

  it("says a 409 saved nothing, and re-reads instead of retrying", async () => {
    const { fetchMock } = await renderPanel({
      definitions: listing([OVERRIDE_ROW]),
      config: overriddenConfig(),
      save: () => json({ detail: "revision conflict" }, 409),
    });

    fireEvent.click(screen.getByRole("button", { name: "Remove Weekly Watched" }));

    expect(await screen.findByText(STALE_SAVE_NOTE)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([p]) => p === "/api/playlists/definitions"),
      ).toHaveLength(2),
    );
    expect(
      fetchMock.mock.calls.filter(([p]) => p === "/api/config/overrides"),
    ).toHaveLength(1);
  });
});
