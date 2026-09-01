/** The Groups panel: the third writer of the overrides document, and the one
 * whose whole value is WHAT it writes.
 *
 * Two things are asserted hardest. The PUT body: a save is always the COMPLETE
 * permutation of every key the server enumerated (partial-list semantics exist
 * server-side, but an explicit full list is what an operator should find
 * persisted), and Reset is the key going AWAY — `withoutPath`, the overrides
 * contract's revert — never a write of any list. And the enumeration source:
 * the fixture serves FOUR groups, not ten, and THREE separator styles, not 22,
 * and every assertion still holds — which is the proof that no group key and no
 * style name lives in the component.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { STALE_SAVE_NOTE } from "../api/overrides";
import { GroupsPanel } from "./GroupsPanel";

/** Four groups, deliberately not ten: the panel must render whatever the
 * server enumerates. Internally consistent as a four-group answer — sections
 * are position × 10, positions are the indexes. */
const GROUPS = [
  { key: "charts", title: "Chart Collections", section: "010", position: 0 },
  { key: "awards", title: "Award Collections", section: "020", position: 1 },
  { key: "content_ratings", title: "Ratings Collections", section: "030", position: 2 },
  { key: "operator", title: "Collections", section: "040", position: 3 },
];

/** Three styles, deliberately not 22: the component renders whatever the
 * server enumerates, the GROUPS fixture's rule. */
const STYLES = ["gold", "orig", "sand"];

function catalog(groups: unknown[] = GROUPS) {
  // The panel reads `groups` and the style pair; the categories belong to the
  // picker.
  return {
    categories: [],
    groups,
    separator_styles: STYLES,
    separator_style: "orig",
  };
}

/** The config GET, whose only job here is seeding the document the panel
 * edits. The unrelated `plex.url` override is the point of the fixture: a
 * save about group order must not drop it. */
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

/** A config that already stores a group_order override, so Reset has
 * something to remove. The served groups order matches it, as the server's
 * effective order would. */
function overriddenConfig() {
  return config({
    collections: {
      enabled: true,
      group_order: ["operator", "charts", "awards", "content_ratings"],
    },
    overridden_paths: ["plex.url", "collections.group_order"],
  });
}

const OVERRIDDEN_GROUPS = [
  { key: "operator", title: "Collections", section: "010", position: 0 },
  { key: "charts", title: "Chart Collections", section: "020", position: 1 },
  { key: "awards", title: "Award Collections", section: "030", position: 2 },
  { key: "content_ratings", title: "Ratings Collections", section: "040", position: 3 },
];

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

/** The document the panel sent, parsed. */
function sentDocument(puts: RequestInit[], index = 0): Record<string, any> {
  const body = puts[index]?.body;
  expect(typeof body).toBe("string");
  return JSON.parse(body as string).document;
}

async function renderPanel(options: StubOptions = {}) {
  const stub = stubFetch(options);
  render(<GroupsPanel />);
  await screen.findByRole("list", { name: "Collection group order" });
  return stub;
}

function rowTitles() {
  const list = screen.getByRole("list", { name: "Collection group order" });
  return within(list)
    .getAllByRole("listitem")
    .map((row) => within(row).getByText(/./, { selector: ".groups-title" }).textContent);
}

async function save() {
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
}

beforeEach(() => {
  setToken(null);
});

describe("the groups panel", () => {
  it("renders the server's groups in the served order, with keys and live section numbers", async () => {
    await renderPanel();

    expect(rowTitles()).toEqual([
      "Chart Collections",
      "Award Collections",
      "Ratings Collections",
      "Collections",
    ]);
    const first = screen.getAllByRole("listitem")[0];
    // The key an operator would write in YAML, and the section the RUNNING
    // config gives the group — both straight off the response.
    expect(within(first).getByText("charts")).toBeInTheDocument();
    expect(within(first).getByText("!010")).toBeInTheDocument();
  });

  it("disables Up on the first row and Down on the last", async () => {
    await renderPanel();

    expect(screen.getByRole("button", { name: "Move Chart Collections up" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Collections down" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Move Award Collections up" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Move Award Collections down" })).toBeEnabled();
  });

  it("moves a row and marks the order unsaved, keeping the served section beside it", async () => {
    await renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));

    expect(rowTitles()).toEqual([
      "Award Collections",
      "Chart Collections",
      "Ratings Collections",
      "Collections",
    ]);
    // The badge is the RUNNING config's number — what the sort titles in Plex
    // carry right now. Renumbering happens on save, and the churn note says
    // so; recomputing it here would be a second copy of the server's
    // arithmetic.
    const first = screen.getAllByRole("listitem")[0];
    expect(within(first).getByText("!020")).toBeInTheDocument();
    expect(screen.getByText(/unsaved/i)).toBeInTheDocument();
  });

  it("saves the complete permutation, keeping unrelated overrides", async () => {
    const { puts } = await renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    await save();

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    // The WHOLE list, not the moved key: what the operator sees is exactly
    // what persists.
    expect(document.collections.group_order).toEqual([
      "awards", "charts", "content_ratings", "operator",
    ]);
    // The unrelated override the config reported is still in the document.
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("disables Save when the order matches the server, including after a move undone", async () => {
    const { puts } = await renderPanel();

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections down" }));

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(puts).toHaveLength(0);
  });

  it("resets by removing the key, never by writing an order", async () => {
    const { puts } = await renderPanel({
      catalog: catalog(OVERRIDDEN_GROUPS),
      config: overriddenConfig(),
    });

    fireEvent.click(screen.getByRole("button", { name: "Reset to config file" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    // The revert IS the absence: `null` is a value the server refuses, and a
    // written canonical list would be an override pretending to be none.
    expect(document.collections).toBeUndefined();
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("disables Reset when no override is stored", async () => {
    await renderPanel();

    expect(screen.getByRole("button", { name: "Reset to config file" })).toBeDisabled();
  });

  it("hands focus to the row's other button when a move lands it at an edge", async () => {
    await renderPanel();

    // Award moves to the top; its Up button becomes disabled, and a disabled
    // element drops keyboard focus on the floor — so the panel hands focus to
    // the same row's Down button.
    const up = screen.getByRole("button", { name: "Move Award Collections up" });
    up.focus();
    fireEvent.click(up);

    expect(screen.getByRole("button", { name: "Move Award Collections down" })).toHaveFocus();
  });

  it("states the live-swap and churn sentences", async () => {
    await renderPanel();

    // C5: the change is live with no restart and lands at the next pass…
    expect(screen.getByText(/no restart/i)).toBeInTheDocument();
    // …and the first pass that sees it rewrites the moved groups' sort
    // titles, one write each, once.
    expect(screen.getByText(/one write per collection, once/i)).toBeInTheDocument();
  });

  it("renders a 422 against the path it names", async () => {
    await renderPanel({
      save: () =>
        json(
          {
            detail: [
              {
                path: "collections.group_order",
                message: "unknown collection group 'chartz'",
              },
            ],
          },
          422,
        ),
    });

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    await save();

    const message = await screen.findByText("unknown collection group 'chartz'");
    // Against the path it names, in the same row: the panel's own copy names
    // that path too, so an unscoped query would pass on the prose alone.
    const row = message.closest("li");
    expect(row).not.toBeNull();
    expect(within(row!).getByText("collections.group_order")).toBeInTheDocument();
  });

  it("re-reads the catalog and config after a save, and reports the versions", async () => {
    const { fetchMock, puts } = await renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    await save();
    await waitFor(() => expect(puts).toHaveLength(1));

    // Which order is stored is the server's answer, not this component's.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([path]) => path === "/api/collections/catalog"),
      ).toHaveLength(2),
    );
    const saved = await screen.findByRole("status");
    expect(saved).toHaveTextContent("cfg-1");
    expect(saved).toHaveTextContent("cfg-2");
  });
});

describe("the style select", () => {
  it("renders the served styles with the running value selected", async () => {
    await renderPanel();

    const select = screen.getByLabelText("Divider style") as HTMLSelectElement;
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent),
    ).toEqual(STYLES);
    expect(select.value).toBe("orig");
    // The preview grid follows the selection.
    const image = screen.getByAltText("orig separator style preview");
    expect(image.getAttribute("src")).toContain("/separators/orig/!_orig_grid.webp");
  });

  it("saves the changed style at its path without touching other overrides", async () => {
    const { puts } = await renderPanel();

    fireEvent.change(screen.getByLabelText("Divider style"), {
      target: { value: "sand" },
    });

    expect(
      screen.getByAltText("sand separator style preview").getAttribute("src"),
    ).toContain("/separators/sand/!_sand_grid.webp");

    await save();
    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    expect(document.collections.separator_style).toBe("sand");
    expect(document.plex).toEqual({ url: "http://plex:32400" });
    // The order was not dirty; it must not be written along for the ride.
    expect(document.collections.group_order).toBeUndefined();
  });

  it("saves a dirty order and a dirty style as one document", async () => {
    const { puts } = await renderPanel();

    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    fireEvent.change(screen.getByLabelText("Divider style"), {
      target: { value: "gold" },
    });

    await save();
    await waitFor(() => expect(puts).toHaveLength(1));
    // One PUT, both keys: two saves would make the second overwrite a
    // document the first had already changed under it.
    const document = sentDocument(puts);
    expect(document.collections.group_order).toEqual([
      "awards", "charts", "content_ratings", "operator",
    ]);
    expect(document.collections.separator_style).toBe("gold");
  });

  it("enables Save on a style change alone, and disables it again when undone", async () => {
    await renderPanel();

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Divider style"), {
      target: { value: "sand" },
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();

    fireEvent.change(screen.getByLabelText("Divider style"), {
      target: { value: "orig" },
    });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("reset removes the style key -- the overrides revert, never a write", async () => {
    const { puts } = await renderPanel({
      // The catalog agrees with the config: the endpoint serves the RUNNING
      // value, so an operator with a stored "sand" sees "sand" selected.
      catalog: { ...catalog(), separator_style: "sand" },
      config: config({
        collections: { enabled: true, separator_style: "sand" },
        overridden_paths: ["plex.url", "collections.separator_style"],
      }),
    });

    fireEvent.click(screen.getByRole("button", { name: "Reset style" }));

    await waitFor(() => expect(puts).toHaveLength(1));
    const document = sentDocument(puts);
    expect(document.collections?.separator_style).toBeUndefined();
    expect(document.plex.url).toBe("http://plex:32400");
  });

  it("disables Reset style when no style override is stored", async () => {
    await renderPanel();

    expect(screen.getByRole("button", { name: "Reset style" })).toBeDisabled();
  });

  it("states the style's own churn disclosure, naming both art kinds", async () => {
    await renderPanel();

    expect(screen.getByText(/re-writes and re-posters every divider once/i))
      .toBeInTheDocument();
    expect(screen.getByText(/textless base layer/i)).toBeInTheDocument();
  });

  it("shows a captioned note instead of a broken image when the preview fails to load", async () => {
    await renderPanel();

    const image = screen.getByAltText("orig separator style preview");
    fireEvent.error(image);

    // No <img> at all: same rule the artwork panes follow for a src that may
    // not load.
    expect(screen.queryByAltText("orig separator style preview")).toBeNull();
    // The load-bearing half: an operator on an air-gapped box must be able
    // to tell "the preview cannot load" from "the style is broken."
    expect(screen.getByText(/the style still applies/i)).toBeInTheDocument();
  });

  it("shows the unsaved badge above the style block, not below the preview", async () => {
    // Divider review T4 M5: the badge used to render under the ~640px style
    // preview, a screen away from the buttons that made the order dirty.
    await renderPanel();

    fireEvent.click(
      screen.getByRole("button", { name: "Move Chart Collections down" }),
    );

    const badge = screen.getByText(/unsaved/i);
    const styleBlock = document.querySelector(".groups-style");
    expect(styleBlock).not.toBeNull();
    expect(
      badge.compareDocumentPosition(styleBlock!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("GroupsPanel stale-save recovery", () => {
  /** This file's move-then-save idiom, matching the neighbouring saving
   * tests -- there is no shared helper for it. */
  async function saveOrder(puts: RequestInit[]) {
    fireEvent.click(screen.getByRole("button", { name: "Move Award Collections up" }));
    await save();
    await waitFor(() => expect(puts).toHaveLength(1));
  }

  it("sends the revision it seeded from", async () => {
    const { puts } = await renderPanel({
      config: { ...config(), overrides_revision: "rev-1" },
    });
    await saveOrder(puts);

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
    await saveOrder(puts);

    await screen.findByText(STALE_SAVE_NOTE);
    expect(puts).toHaveLength(1);
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([path]) => path === "/api/config"),
      ).toHaveLength(2),
    );
  });
});
