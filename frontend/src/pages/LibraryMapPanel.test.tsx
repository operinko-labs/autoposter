/** The library map: what it reads, what it sends, and what it renders when
 * the server refuses.
 *
 * The stub routes by URL AND method, the way the server card's does, and
 * answers a 500 that names anything a test has not declared -- the panel
 * reaches four routes, two of them POSTs to sibling addresses and one a PUT,
 * so a stub that answered one body for every request would be asserting
 * against itself.
 *
 * Every refusal sentence asserted here is copied from
 * `src/autoposter/api/servers.py` rather than paraphrased: the panel's promise
 * is that the server's own sentence reaches the operator verbatim, and a test
 * that asserted a paraphrase would pass over a panel that rewrote it.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LibraryMapPanel } from "./LibraryMapPanel";
import { RESTART_NOTE } from "../api/overrides";

const PLEX_LIBRARIES = {
  libraries: [
    { id: "1", name: "Movies", kind: "movie" },
    { id: "2", name: "TV Shows", kind: "show" },
  ],
};

const JELLYFIN_LIBRARIES = {
  libraries: [
    { id: "a", name: "Films", kind: "movies" },
    { id: "b", name: "TV Shows", kind: "tvshows" },
  ],
};

const CONFIG = {
  jellyfin: { library_map: { Movies: "Films" } },
  overrides_revision: "r1",
};

const SAVED = {
  version_before: "a",
  version_after: "b",
  restart_required: ["jellyfin.library_map.Movies"],
};

/** `NOT_A_LIBRARY_THIS_SERVER_LISTS`, rendered with Jellyfin's label. */
const LISTS_NO_LIBRARY = "Jellyfin lists no library called that";

/** `LIBRARY_MAP_NEEDS_BOTH_CREDENTIALS`, verbatim. */
const NEEDS_BOTH_CREDENTIALS =
  "the library map is read from both servers' own library lists; set both " +
  "servers' credentials before setting it";

/** `DELTA_STORE_CANNOT_MAP`, verbatim. */
const DELTA_STORE_CANNOT_MAP =
  "this deployment still stores its settings as changes to the mounted " +
  "configuration file, and a library map written into those would be added " +
  "to the file's own pairs rather than replacing them; restart this " +
  "deployment, which converts the stored settings into a whole document, " +
  "then set the map";

/** `LIBRARY_MAP_PAIRS_ONCE`, rendered with a Jellyfin library's name. */
const PAIRS_ONCE = "Films is already paired with another Plex library";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Call {
  url: string;
  method: string;
  body: unknown;
}

type Route = (init: RequestInit | undefined) => Response;

/** Stub `fetch` with one handler per `"METHOD /path"`, and record every call.
 *
 * The three reads the panel makes when it opens are seeded here, because every
 * test needs them open; a test that cares about one of them declares its own
 * over the top. */
function router(routes: Record<string, Route> = {}): Call[] {
  const calls: Call[] = [];
  const table: Record<string, Route> = {
    "GET /api/config": () => json(CONFIG),
    "POST /api/servers/plex/libraries": () => json(PLEX_LIBRARIES),
    "POST /api/servers/jellyfin/libraries": () => json(JELLYFIN_LIBRARIES),
    ...routes,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({
        url,
        method,
        body:
          init?.body === undefined || init?.body === null
            ? undefined
            : JSON.parse(String(init.body)),
      });
      const route = table[`${method} ${url}`];
      return Promise.resolve(
        route === undefined
          ? json({ detail: `the panel asked for ${method} ${url}` }, 500)
          : route(init),
      );
    }),
  );
  return calls;
}

async function renderPanel(
  {
    revision = "r1",
    onChanged = () => {},
  }: { revision?: string | null; onChanged?: () => void } = {},
) {
  const panel = (open: boolean) => (
    <LibraryMapPanel
      revision={revision}
      onChanged={onChanged}
      open={open}
      onToggle={() => {}}
    />
  );
  // A closed section reads nothing, so the first render needs no act: the tab
  // owns `open`, and opening the section is the parent handing down a new
  // value rather than this panel's own state changing.
  const rendered = render(panel(false));
  await act(async () => {
    rendered.rerender(panel(true));
  });
}

function optionsOf(label: string): string[] {
  return Array.from(
    screen.getByLabelText(label).querySelectorAll("option"),
  ).map((option) => option.textContent ?? "");
}

function optionNamed(label: string, text: string): HTMLOptionElement {
  const found = Array.from(
    screen.getByLabelText(label).querySelectorAll("option"),
  ).find((option) => option.textContent === text);
  if (found === undefined) throw new Error(`${label} has no option "${text}"`);
  return found as HTMLOptionElement;
}

async function save() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save the map" }));
  });
}

function bodyOf(calls: Call[], method: string, url: string): unknown {
  const found = calls.find((call) => call.method === method && call.url === url);
  if (found === undefined) throw new Error(`no ${method} ${url} was sent`);
  return found.body;
}

describe("LibraryMapPanel", () => {
  it("reads both servers' own listings, asking each for its booted address", async () => {
    const calls = router();
    await renderPanel();
    // An empty body is what asks for the address this deployment booted with
    // and the credential it holds; anything else would be an address this
    // panel named, which it has none of.
    expect(bodyOf(calls, "POST", "/api/servers/plex/libraries")).toEqual({});
    expect(bodyOf(calls, "POST", "/api/servers/jellyfin/libraries")).toEqual({});
  });

  it("renders one row per Plex library, each a dropdown of Jellyfin's", async () => {
    router();
    await renderPanel();
    expect(screen.getByLabelText("Movies").tagName).toBe("SELECT");
    expect(optionsOf("Movies")).toEqual(["(not paired)", "Films", "TV Shows"]);
    expect(optionsOf("TV Shows")).toEqual(["(not paired)", "Films", "TV Shows"]);
  });

  it("accepts no name that is not on a dropdown", async () => {
    router();
    await renderPanel();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("shows the stored pair, and shows a same-named library as paired", async () => {
    router();
    await renderPanel();
    expect(screen.getByLabelText("Movies")).toHaveValue("Films");
    // Nothing stores this pair -- the server drops a row whose two names
    // agree -- but showing it as "not paired" would invite a fix for
    // something that is not broken.
    expect(screen.getByLabelText("TV Shows")).toHaveValue("TV Shows");
  });

  it("takes a Jellyfin library another row has claimed off the other dropdowns", async () => {
    router();
    await renderPanel();
    expect(optionNamed("Movies", "TV Shows").disabled).toBe(true);
    expect(optionNamed("Movies", "Films").disabled).toBe(false);
    expect(optionNamed("TV Shows", "Films").disabled).toBe(true);
    expect(optionNamed("TV Shows", "TV Shows").disabled).toBe(false);
  });

  it("clears one row without touching the others", async () => {
    router();
    await renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Clear Movies" }));
    expect(screen.getByLabelText("Movies")).toHaveValue("");
    expect(screen.getByLabelText("TV Shows")).toHaveValue("TV Shows");
  });

  it("sends the whole map with the revision, and leaves an unpaired row out", async () => {
    const calls = router({
      "PUT /api/servers/jellyfin/library-map": () => json(SAVED),
    });
    await renderPanel();
    fireEvent.change(screen.getByLabelText("TV Shows"), { target: { value: "" } });
    await save();
    expect(bodyOf(calls, "PUT", "/api/servers/jellyfin/library-map")).toEqual({
      pairs: { Movies: "Films" },
      expected_revision: "r1",
      confirm: false,
    });
  });

  it("sends an empty map when every row has been cleared", async () => {
    const calls = router({
      "PUT /api/servers/jellyfin/library-map": () => json(SAVED),
    });
    await renderPanel();
    fireEvent.click(screen.getByRole("button", { name: "Clear Movies" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear TV Shows" }));
    await save();
    expect(bodyOf(calls, "PUT", "/api/servers/jellyfin/library-map")).toEqual({
      pairs: {},
      expected_revision: "r1",
      confirm: false,
    });
  });

  it("says a saved map waits for a restart, and has the page re-read", async () => {
    const onChanged = vi.fn();
    router({ "PUT /api/servers/jellyfin/library-map": () => json(SAVED) });
    await renderPanel({ onChanged });
    await save();
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByText(new RegExp(RESTART_NOTE))).toBeInTheDocument();
  });

  it("renders a refused listing verbatim and stays usable", async () => {
    router({
      "POST /api/servers/jellyfin/libraries": () =>
        json({ detail: "Jellyfin refused the credential this deployment holds." }, 502),
    });
    await renderPanel();
    expect(
      screen.getByText("Jellyfin refused the credential this deployment holds."),
    ).toBeInTheDocument();
    // Plex's listing arrived, so its rows are here -- with nothing to pair
    // them to yet, which is the honest rendering of one refused read.
    expect(optionsOf("Movies")).toEqual(["(not paired)"]);
    expect(screen.getByRole("button", { name: "Save the map" })).toBeEnabled();
  });

  it("does not claim Plex listed nothing when Plex's own read was refused", async () => {
    router({
      "POST /api/servers/plex/libraries": () =>
        json({ detail: "Plex could not be reached (ConnectError)." }, 502),
    });
    await renderPanel();
    expect(
      screen.getByText("Plex could not be reached (ConnectError)."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/listed no libraries/)).not.toBeInTheDocument();
  });

  it("names the row a refused pair belongs to, beside the server's sentence", async () => {
    router({
      "PUT /api/servers/jellyfin/library-map": () =>
        json(
          {
            detail: [
              {
                path: "jellyfin.library_map",
                library: "Movies",
                message: LISTS_NO_LIBRARY,
              },
            ],
          },
          422,
        ),
    });
    await renderPanel();
    await save();
    expect(screen.getByText(`Movies: ${LISTS_NO_LIBRARY}`)).toBeInTheDocument();
  });

  it("renders the server's own sentence when one Jellyfin library is claimed twice", async () => {
    router({
      "PUT /api/servers/jellyfin/library-map": () =>
        json(
          {
            detail: [
              {
                path: "jellyfin.library_map",
                library: "TV Shows",
                message: PAIRS_ONCE,
              },
            ],
          },
          422,
        ),
    });
    await renderPanel();
    await save();
    expect(screen.getByText(`TV Shows: ${PAIRS_ONCE}`)).toBeInTheDocument();
  });

  it("renders the refusal of a map on a server with no credential", async () => {
    router({
      "PUT /api/servers/jellyfin/library-map": () =>
        json({ detail: NEEDS_BOTH_CREDENTIALS }, 409),
    });
    await renderPanel();
    await save();
    expect(screen.getByText(NEEDS_BOTH_CREDENTIALS)).toBeInTheDocument();
  });

  it("renders the refusal of a map on a store that still holds a delta", async () => {
    router({
      "PUT /api/servers/jellyfin/library-map": () =>
        json({ detail: DELTA_STORE_CANNOT_MAP }, 409),
    });
    await renderPanel();
    await save();
    expect(screen.getByText(DELTA_STORE_CANNOT_MAP)).toBeInTheDocument();
  });

  it("cannot save a page that was served no revision", async () => {
    router();
    await renderPanel({ revision: null });
    expect(screen.getByRole("button", { name: "Save the map" })).toBeDisabled();
    expect(screen.getByText(/reload the page before saving/)).toBeInTheDocument();
  });
});
