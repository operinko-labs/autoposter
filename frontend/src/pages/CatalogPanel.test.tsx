/** The catalog picker: the app's first tab component, and its second writer of
 * the overrides document.
 *
 * Two things here are worth more than the rest and are asserted hardest.
 *
 *  - **Which path a row writes.** A row backed by a `setting` writes that
 *    config boolean; every other row is a member of `collections.presets`.
 *    Routing one through the other is not a cosmetic bug: the server refuses
 *    `presets: [oscars]` as an unknown key, and a `collections.awards: true`
 *    that should have been a preset key silently switches on a different
 *    family of collections. So the captured PUT body is asserted for both
 *    kinds, including the key that must NOT be in it.
 *  - **The tab semantics.** Nothing else in the app has tabs, so there is no
 *    neighbour to notice a regression here. The roles, the selected state and
 *    the arrow keys are pinned directly.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { STALE_SAVE_NOTE } from "../api/overrides";
import { CatalogPanel } from "./CatalogPanel";

/** The server always lists ten categories, in its own order -- `franchises` is
 * the newest, which is why it is ten and not nine. This fixture holds the six
 * categories that get no rows here, which is an ordinary state and not an
 * error -- the picker shows the tab anyway, because a category that vanished
 * when its rows were unwritten would read as a category this service does not
 * have. `franchises` is deliberately absent from the fixture rather than added
 * empty: it carries rows in the real catalog, so an empty one here would pin a
 * state the server never serves. */
const EMPTY_CATEGORIES = [
  { key: "content", label: "Content" },
  { key: "location", label: "Location" },
  { key: "media", label: "Media" },
  { key: "people", label: "People" },
  { key: "production", label: "Production" },
  { key: "time", label: "Time" },
];

function preset(overrides: Record<string, unknown> = {}) {
  return {
    key: "award_cannes",
    name: "Cannes Film Festival",
    titles: ["Cannes Palme d'Or Winners"],
    years_title: 'one per ceremony, named "Cannes <year>"',
    description: "Cannes Film Festival: 1 winners collection, plus one collection per recent ceremony.",
    kometa_source: "defaults/award/cannes.yml",
    library_types: ["Movie"],
    readiness: "ready",
    gated_row: null,
    setting: null,
    active: false,
    ...overrides,
  };
}

/** `active` per key, defaulting to off. The catalog reports the live config's
 * answer for every row, so a fixture that wants a row switched on says so here
 * rather than the test reaching into the component. */
function catalog(active: Record<string, boolean> = {}) {
  const on = (key: string) => active[key] ?? false;
  return {
    categories: [
      {
        key: "awards",
        label: "Awards",
        presets: [
          preset({ active: on("award_cannes") }),
          preset({
            key: "award_berlinale",
            name: "Berlin International Film Festival",
            titles: ["Berlinale Golden Bear Winners"],
            years_title: 'one per ceremony, named "Berlinale <year>"',
            description: "Berlin International Film Festival: 1 winners collection.",
            kometa_source: "defaults/award/berlinale.yml",
            active: on("award_berlinale"),
          }),
          preset({
            key: "oscars",
            name: "Academy Awards",
            titles: ["Best Picture Winners"],
            description: "Switched on by the collections.awards setting, not by a preset key.",
            kometa_source: "defaults/award/oscars.yml",
            setting: "collections.awards",
            active: on("oscars"),
          }),
          preset({
            key: "award_notyet",
            name: "A ceremony still being built",
            titles: [],
            years_title: null,
            description: "The table knows what this would be; the builder has not landed.",
            kometa_source: "defaults/award/notyet.yml",
            readiness: "gated",
            gated_row: 155,
            active: false,
          }),
        ],
      },
      {
        key: "charts",
        label: "Charts",
        presets: [
          preset({
            key: "imdb_charts",
            name: "IMDb Charts",
            titles: ["IMDb Popular", "IMDb Top 250"],
            years_title: null,
            description: "The chart family collections.charts already builds.",
            kometa_source: "defaults/chart/imdb.yml",
            library_types: ["Movie", "Show"],
            setting: "collections.charts",
            active: on("imdb_charts"),
          }),
        ],
      },
      { key: "content_ratings", label: "Content Ratings", presets: [] },
      ...EMPTY_CATEGORIES.map((category) => ({ ...category, presets: [] })),
    ],
  };
}

/** The config GET, whose only job here is seeding the document the picker
 * edits. `overridden_paths` carrying an unrelated override is the point of the
 * fixture: a save about collections must not drop it. */
function config(overrides: Record<string, unknown> = {}) {
  return {
    version: "cfg-1",
    plex: { url: "http://plex:32400" },
    collections: { enabled: true, awards: false, charts: false, presets: [] },
    overridden_paths: ["plex.url"],
    frozen_paths: {},
    redacted_paths: [],
    keep_sentinel: "***KEEP***",
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface StubOptions {
  catalog?: unknown;
  config?: unknown;
  save?: (init?: RequestInit) => Response;
}

function stubFetch(options: StubOptions = {}) {
  const puts: RequestInit[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/collections/catalog") return json(options.catalog ?? catalog());
    if (path === "/api/config") return json(options.config ?? config());
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

/** The document the picker sent, parsed. */
function sentDocument(puts: RequestInit[], index = 0): Record<string, any> {
  const body = puts[index]?.body;
  expect(typeof body).toBe("string");
  return JSON.parse(body as string).document;
}

async function renderPanel(options: StubOptions = {}) {
  const stub = stubFetch(options);
  render(<CatalogPanel />);
  // The tab strip is the last thing to appear: it needs the catalog.
  await screen.findByRole("tablist");
  return stub;
}

function tabs() {
  return screen.getAllByRole("tab");
}

async function save() {
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
}

beforeEach(() => {
  setToken(null);
});

describe("the catalog picker's tab strip", () => {
  it("renders a tab per category the API lists, the first one selected", async () => {
    await renderPanel();

    // Nine, and from the response -- never a hard-coded list. A category the
    // server grows appears here without this file being touched.
    expect(tabs().map((tab) => tab.textContent)).toEqual([
      "Awards",
      "Charts",
      "Content Ratings",
      "Content",
      "Location",
      "Media",
      "People",
      "Production",
      "Time",
    ]);
    expect(tabs()[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs()[1]).toHaveAttribute("aria-selected", "false");
  });

  it("wires each tab to the panel it controls", async () => {
    await renderPanel();

    const panel = screen.getByRole("tabpanel");
    expect(tabs()[0]).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", tabs()[0].id);
    // Exactly one panel is rendered: the unselected categories are absent from
    // the tree, not merely hidden, so a screen reader cannot walk into them.
    expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
    // Which is exactly why an unselected tab claims no panel: the id it would
    // name is not in the document, and a dangling IDREF is a broken promise to
    // the screen reader rather than an unused attribute.
    expect(tabs()[1]).not.toHaveAttribute("aria-controls");
  });

  it("keeps one tab stop in the strip and moves it with the selection", async () => {
    await renderPanel();

    expect(tabs()[0]).toHaveAttribute("tabindex", "0");
    expect(tabs()[1]).toHaveAttribute("tabindex", "-1");

    fireEvent.keyDown(tabs()[0], { key: "ArrowRight" });

    expect(tabs()[1]).toHaveAttribute("tabindex", "0");
    expect(tabs()[0]).toHaveAttribute("tabindex", "-1");
  });

  it("moves selection and focus with the arrow keys, wrapping at both ends", async () => {
    await renderPanel();

    fireEvent.keyDown(tabs()[0], { key: "ArrowRight" });
    expect(tabs()[1]).toHaveAttribute("aria-selected", "true");
    expect(tabs()[1]).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs()[1].id);

    // Left from the first wraps to the last rather than stopping dead.
    fireEvent.keyDown(tabs()[1], { key: "ArrowLeft" });
    fireEvent.keyDown(tabs()[0], { key: "ArrowLeft" });
    expect(tabs()[8]).toHaveAttribute("aria-selected", "true");
    expect(tabs()[8]).toHaveFocus();

    // And right from the last wraps back to the first.
    fireEvent.keyDown(tabs()[8], { key: "ArrowRight" });
    expect(tabs()[0]).toHaveAttribute("aria-selected", "true");
  });

  it("jumps to the ends with Home and End", async () => {
    await renderPanel();

    fireEvent.keyDown(tabs()[0], { key: "End" });
    expect(tabs()[8]).toHaveAttribute("aria-selected", "true");
    expect(tabs()[8]).toHaveFocus();

    fireEvent.keyDown(tabs()[8], { key: "Home" });
    expect(tabs()[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs()[0]).toHaveFocus();
  });

  it("shows the selected category's rows and only those", async () => {
    await renderPanel();

    expect(screen.getByRole("checkbox", { name: /Cannes Film Festival/ })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /IMDb Charts/ })).toBeNull();

    fireEvent.click(tabs()[1]);

    expect(screen.getByRole("checkbox", { name: /IMDb Charts/ })).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /Cannes Film Festival/ })).toBeNull();
  });

  it("says so in a category whose rows have not been written yet", async () => {
    await renderPanel();

    fireEvent.click(screen.getByRole("tab", { name: "Location" }));

    const panel = screen.getByRole("tabpanel");
    expect(within(panel).queryAllByRole("checkbox")).toHaveLength(0);
    expect(within(panel).getByText(/Nothing in this category yet/)).toBeInTheDocument();
  });
});

describe("a catalog row", () => {
  it("carries its description, its Kometa provenance and what it builds", async () => {
    await renderPanel();

    const row = screen.getByRole("checkbox", { name: /Cannes Film Festival/ }).closest("li");
    expect(row).not.toBeNull();
    const inside = within(row!);
    expect(inside.getByText(/1 winners collection/)).toBeInTheDocument();
    // Verbatim, because it is a claim about a specific upstream file: a
    // paraphrase would make it unverifiable.
    expect(inside.getByText("defaults/award/cannes.yml")).toBeInTheDocument();
    expect(inside.getByText(/Cannes Palme d'Or Winners/)).toBeInTheDocument();
    // The dynamic titles are reported as a shape, never as titles that exist
    // -- which years the dataset carries is not knowable here.
    expect(inside.getByText(/Cannes <year>/)).toBeInTheDocument();
  });

  it("names the setting a setting-backed row throws, so the row is not read as a preset", async () => {
    await renderPanel();

    const row = screen.getByRole("checkbox", { name: /Academy Awards/ }).closest("li");
    expect(within(row!).getByText("collections.awards")).toBeInTheDocument();
  });

  it("disables a gated row and names the row it waits on", async () => {
    await renderPanel();

    const box = screen.getByRole("checkbox", { name: /A ceremony still being built/ });
    expect(box).toBeDisabled();
    expect(within(box.closest("li")!).getByText("needs row 155")).toBeInTheDocument();
  });
});

describe("saving the picker's choices", () => {
  it("PUTs a preset row as a member of collections.presets", async () => {
    const { puts } = await renderPanel();

    fireEvent.click(screen.getByRole("checkbox", { name: /Cannes Film Festival/ }));
    await save();

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    expect(document.collections.presets).toEqual(["award_cannes"]);
    // A preset is not a setting. If the routing were reversed this key would
    // be here, and the config it produced would switch on the Oscars.
    expect(document.collections.awards).toBeUndefined();
    // The unrelated override the config reported is still in the document: a
    // save about collections must not drop what another page stored.
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("PUTs a setting-backed row as its own config boolean, never as a preset key", async () => {
    const { puts } = await renderPanel();

    fireEvent.click(screen.getByRole("checkbox", { name: /Academy Awards/ }));
    await save();

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    expect(document.collections.awards).toBe(true);
    // The whole point. `presets: [oscars]` is refused by the server as an
    // unknown key, so a document carrying one is a 422 waiting to happen --
    // and nothing else in the picker would notice.
    expect(document.collections.presets).toBeUndefined();
  });

  it("writes an explicit false when a setting-backed row is switched off", async () => {
    // The off direction of the same route. It cannot be expressed by dropping
    // the key: the document is a delta, so a missing `collections.awards`
    // reverts to whatever the config file says -- which is `true` for anyone
    // who switched the Oscars on there. Only an explicit `false` turns them off.
    const { puts } = await renderPanel({
      catalog: catalog({ oscars: true }),
      config: config({
        collections: { enabled: true, awards: true, charts: false, presets: [] },
        overridden_paths: ["collections.awards"],
      }),
    });

    const box = screen.getByRole("checkbox", { name: /Academy Awards/ });
    expect(box).toBeChecked();
    fireEvent.click(box);
    await save();

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    expect(document.collections.awards).toBe(false);
    expect(document.collections.presets).toBeUndefined();
  });

  it("writes the boolean a setting-backed row in another category names", async () => {
    const { puts } = await renderPanel();

    fireEvent.click(screen.getByRole("tab", { name: "Charts" }));
    fireEvent.click(screen.getByRole("checkbox", { name: /IMDb Charts/ }));
    await save();

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    expect(document.collections.charts).toBe(true);
    expect(document.collections.awards).toBeUndefined();
    expect(document.collections.presets).toBeUndefined();
  });

  it("drops an unchecked preset from the list it sends, keeping the rest", async () => {
    const { puts } = await renderPanel({
      catalog: catalog({ award_cannes: true, award_berlinale: true }),
      config: config({
        collections: {
          enabled: true,
          awards: false,
          charts: false,
          presets: ["award_cannes", "award_berlinale"],
        },
        overridden_paths: ["collections.presets"],
      }),
    });

    fireEvent.click(screen.getByRole("checkbox", { name: /Cannes Film Festival/ }));
    await save();

    await waitFor(() => expect(puts).toHaveLength(1));
    expect(sentDocument(puts).collections.presets).toEqual(["award_berlinale"]);
  });

  it("sends nothing about a row switched on and off again", async () => {
    const { puts } = await renderPanel();

    const box = screen.getByRole("checkbox", { name: /Cannes Film Festival/ });
    fireEvent.click(box);
    fireEvent.click(box);

    // Back to what the server holds, so there is nothing to save and the
    // button says so rather than writing an override that changes nothing.
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(puts).toHaveLength(0);
  });

  it("reports the versions the save moved between, and when it takes effect", async () => {
    await renderPanel();

    fireEvent.click(screen.getByRole("checkbox", { name: /Cannes Film Festival/ }));
    await save();

    const saved = await screen.findByRole("status");
    expect(saved).toHaveTextContent("cfg-1");
    expect(saved).toHaveTextContent("cfg-2");
    expect(saved).toHaveTextContent(/next reconcile/i);
  });

  it("says the config preview reports no artwork impact here by design", async () => {
    await renderPanel();

    // The server answers `impact: null` for a collections-only edit because
    // no rendered image can change, not because the number is missing. The
    // panel says which, so nobody files it as a bug.
    expect(screen.getByText(/by design/i)).toHaveTextContent(/no artwork/i);
  });

  it("re-reads the catalog after a save rather than trusting its own state", async () => {
    const { fetchMock, puts } = await renderPanel();

    fireEvent.click(screen.getByRole("checkbox", { name: /Cannes Film Festival/ }));
    await save();
    await waitFor(() => expect(puts).toHaveLength(1));

    // Which keys are on now is the server's answer, not this component's.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([path]) => path === "/api/collections/catalog"),
      ).toHaveLength(2),
    );
  });

  it("renders a 422 against the path it names", async () => {
    await renderPanel({
      save: () =>
        json(
          {
            detail: [
              {
                path: "collections.presets",
                message: "unknown preset key: award_cannes",
              },
            ],
          },
          422,
        ),
    });

    fireEvent.click(screen.getByRole("checkbox", { name: /Cannes Film Festival/ }));
    await save();

    expect(await screen.findByText("unknown preset key: award_cannes")).toBeInTheDocument();
  });

  it("surfaces a non-422 failure as its message", async () => {
    await renderPanel({ save: () => json({ detail: "the config file is read-only" }, 503) });

    fireEvent.click(screen.getByRole("checkbox", { name: /Cannes Film Festival/ }));
    await save();

    expect(await screen.findByText("the config file is read-only")).toBeInTheDocument();
  });
});

describe("the active summary", () => {
  it("counts what is switched on, per category and in total", async () => {
    await renderPanel({ catalog: catalog({ award_cannes: true, imdb_charts: true }) });

    const summary = screen.getByRole("group", { name: /switched on/i });
    expect(within(summary).getByText("2 active")).toBeInTheDocument();
    expect(within(summary).getByText("Awards 1")).toBeInTheDocument();
    expect(within(summary).getByText("Charts 1")).toBeInTheDocument();
    // A category with nothing on is not listed as a zero -- nine zeroes would
    // bury the one number that matters.
    expect(within(summary).queryByText(/Location/)).toBeNull();
  });

  it("counts what is pending, not what is stored, and says the two differ", async () => {
    await renderPanel();

    const summary = screen.getByRole("group", { name: /switched on/i });
    expect(within(summary).getByText("0 active")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: /Cannes Film Festival/ }));

    expect(within(summary).getByText("1 active")).toBeInTheDocument();
    expect(within(summary).getByText(/unsaved/i)).toBeInTheDocument();
  });
});

describe("CatalogPanel stale-save recovery", () => {
  /** This file's "click a category row and save" idiom, matching the
   * neighbouring saving tests -- there is no shared helper for it. */
  async function toggleFirstKey(puts: RequestInit[]) {
    fireEvent.click(screen.getByRole("checkbox", { name: /Cannes Film Festival/ }));
    await save();
    await waitFor(() => expect(puts).toHaveLength(1));
  }

  it("sends the revision it seeded from", async () => {
    const { puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
    });
    await toggleFirstKey(puts);

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
            changed_paths: ["collections.separator_style"],
          },
          409,
        ),
    });
    await toggleFirstKey(puts);

    await screen.findByText(STALE_SAVE_NOTE);
    expect(puts).toHaveLength(1);
    // Re-read, so the panel is showing the settings that actually hold.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([path]) => path === "/api/config"),
      ).toHaveLength(2),
    );
  });
});
