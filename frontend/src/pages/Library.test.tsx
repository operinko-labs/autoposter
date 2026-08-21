import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Library } from "./Library";

/** Deliberately not the libraries the mocked page of items uses: the filter
 * controls must come from /api/items/filters, and a control populated from the
 * current page could never offer "Documentaries". */
const FILTERS = {
  libraries: ["Documentaries", "Movies", "TV Shows"],
  kinds: ["episode", "movie", "show"],
  statuses: ["failed", "no_art", "rendered"],
};

/** `total` is far larger than `items` so the pager has somewhere to go -- the
 * page is one screen of a large library, which is the whole point of paging
 * against `total` rather than fetching everything. */
const ITEMS = {
  total: 120,
  items: [
    {
      id: 3,
      title: "Ghostbusters",
      library: "Movies",
      kind: "movie",
      rating_key: "101",
      render_status: { poster: "rendered" },
    },
    {
      id: 7,
      title: "Arcane",
      library: "TV Shows",
      kind: "show",
      rating_key: "202",
      render_status: {},
    },
  ],
};

/** Only item 3 has artwork on disk. Item 7 is an ordinary un-rendered item,
 * which the endpoint answers with a 404. */
const ARTWORK: Record<string, string> = {
  "3/poster": "jpeg-bytes-for-ghostbusters",
};

const ARTWORK_PATH = /^\/api\/items\/(\d+)\/artwork\/([a-z_]+)$/;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch() {
  const fetchMock = vi.fn(async (path: string) => {
    if (path === "/api/items/filters") return json(FILTERS);
    if (path.startsWith("/api/items?")) return json(ITEMS);
    const artwork = ARTWORK_PATH.exec(path);
    if (artwork !== null) {
      const body = ARTWORK[`${artwork[1]}/${artwork[2]}`];
      if (body === undefined) return json({ detail: "artwork not found" }, 404);
      return new Response(body, {
        status: 200,
        headers: { "Content-Type": "image/jpeg" },
      });
    }
    throw new Error(`the page requested an unexpected path: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Every element the page has asked to be told about, with the callback that
 * says it is now on screen. Held here rather than fired from `observe()` so a
 * test can assert what happens *before* a tile is scrolled to. */
let observed: { fire: () => void }[] = [];

class FakeIntersectionObserver {
  constructor(private readonly callback: IntersectionObserverCallback) {}

  observe(element: Element): void {
    observed.push({
      fire: () =>
        this.callback(
          [{ isIntersecting: true, target: element } as IntersectionObserverEntry],
          this as unknown as IntersectionObserver,
        ),
    });
  }

  unobserve(): void {}

  disconnect(): void {}
}

/** React flushes passive effects after the commit, and `findBy*` resolves on
 * the commit -- so a tile is in the document a tick before its effect has
 * registered its observer. */
async function flushEffects(): Promise<void> {
  await act(async () => {});
}

async function scrollTilesIntoView(): Promise<void> {
  await flushEffects();
  const entries = observed;
  observed = [];
  await act(async () => {
    for (const entry of entries) entry.fire();
  });
}

/** Object URL -> the blob it was made from, so a test can prove an <img> shows
 * the bytes the server actually returned for that item rather than merely
 * echoing whatever this stub handed back. */
const objectUrls = new Map<string, Blob>();
const realCreateObjectURL = URL.createObjectURL;
const realRevokeObjectURL = URL.revokeObjectURL;

/** Awaits the grid, then loads every tile's artwork. */
async function renderLibrary() {
  const view = render(
    <MemoryRouter>
      <Library />
    </MemoryRouter>,
  );
  await screen.findByRole("link", { name: /Ghostbusters/ });
  await scrollTilesIntoView();
  return view;
}

function tileFor(title: string): HTMLElement {
  return screen.getByRole("link", { name: new RegExp(title) });
}

function pathsMatching(
  fetchMock: ReturnType<typeof stubFetch>,
  predicate: (path: string) => boolean,
): string[] {
  return fetchMock.mock.calls.map((call) => call[0] as string).filter(predicate);
}

beforeEach(() => {
  setToken(null);
  observed = [];
  objectUrls.clear();
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  // jsdom implements neither of these, and `vi.stubGlobal` cannot reach them:
  // they are statics on `URL`, not globals of their own.
  URL.createObjectURL = vi.fn((blob: Blob) => {
    const url = `blob:artwork-${objectUrls.size + 1}`;
    objectUrls.set(url, blob);
    return url;
  });
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  URL.createObjectURL = realCreateObjectURL;
  URL.revokeObjectURL = realRevokeObjectURL;
});

describe("Library", () => {
  it("renders a tile per item, showing the artwork bytes the server returned", async () => {
    const fetchMock = stubFetch();

    await renderLibrary();

    expect(tileFor("Ghostbusters")).toHaveAttribute("href", "/items/3");
    expect(tileFor("Arcane")).toHaveAttribute("href", "/items/7");

    expect(pathsMatching(fetchMock, (path) => ARTWORK_PATH.test(path))).toEqual([
      "/api/items/3/artwork/poster",
      "/api/items/7/artwork/poster",
    ]);

    // Not "the src equals what the stub returned" -- that would only prove the
    // stub works. The object URL is resolved back to its blob and the bytes are
    // compared against what /api/items/3/artwork/poster answered.
    const image = tileFor("Ghostbusters").querySelector("img");
    expect(image).not.toBeNull();
    expect(image).toHaveAttribute("loading", "lazy");
    const blob = objectUrls.get(image!.getAttribute("src") ?? "");
    expect(blob).toBeDefined();
    expect(await blob!.text()).toBe("jpeg-bytes-for-ghostbusters");
  });

  it("populates the filter controls from /api/items/filters", async () => {
    stubFetch();

    await renderLibrary();

    // "Documentaries" appears in no item on the page, so a control derived from
    // the current results could not offer it.
    const librarySelect = screen.getByLabelText("Library") as HTMLSelectElement;
    expect([...librarySelect.options].map((option) => option.value)).toEqual([
      "",
      "Documentaries",
      "Movies",
      "TV Shows",
    ]);

    const kindSelect = screen.getByLabelText("Kind") as HTMLSelectElement;
    expect([...kindSelect.options].map((option) => option.value)).toEqual([
      "",
      "episode",
      "movie",
      "show",
    ]);

    const statusSelect = screen.getByLabelText("Status") as HTMLSelectElement;
    expect([...statusSelect.options].map((option) => option.value)).toEqual([
      "",
      "failed",
      "no_art",
      "rendered",
    ]);
  });

  it("refetches from offset 0 when a filter changes, not from the current page", async () => {
    const fetchMock = stubFetch();

    await renderLibrary();

    const listings = () =>
      pathsMatching(fetchMock, (path) => path.startsWith("/api/items?"));

    expect(listings()).toEqual(["/api/items?limit=48&offset=0"]);

    // Move off the first page first, so a filter change that merely kept the
    // offset would be visible.
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(listings()).toEqual([
        "/api/items?limit=48&offset=0",
        "/api/items?limit=48&offset=48",
      ]),
    );

    fireEvent.change(screen.getByLabelText("Library"), {
      target: { value: "Documentaries" },
    });

    await waitFor(() =>
      expect(listings()).toEqual([
        "/api/items?limit=48&offset=0",
        "/api/items?limit=48&offset=48",
        "/api/items?limit=48&offset=0&library=Documentaries",
      ]),
    );
  });

  it("shows the title rather than a broken image when the artwork 404s", async () => {
    stubFetch();

    await renderLibrary();

    const tile = tileFor("Arcane");
    // No <img> at all: an <img> whose src cannot be loaded is precisely the
    // broken-image icon this has to avoid.
    expect(tile.querySelector("img")).toBeNull();
    const placeholder = tile.querySelector(".tile-art");
    expect(placeholder).not.toBeNull();
    // Exactly the title -- `toHaveTextContent` is a substring match, so a
    // placeholder reading only "no artwork" would satisfy a looser assertion.
    expect(placeholder!.textContent).toBe("Arcane");
  });

  it("requests no artwork for a tile that has not been scrolled to", async () => {
    const fetchMock = stubFetch();

    render(
      <MemoryRouter>
        <Library />
      </MemoryRouter>,
    );
    await screen.findByRole("link", { name: /Ghostbusters/ });
    await flushEffects();

    // Both tiles are watching -- so this is the gate holding the requests back,
    // not the observers having failed to register at all.
    expect(observed).toHaveLength(2);
    expect(pathsMatching(fetchMock, (path) => ARTWORK_PATH.test(path))).toEqual([]);
  });

  it("reports a failed listing instead of an endless spinner", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "database is down" }, 500)),
    );

    render(
      <MemoryRouter>
        <Library />
      </MemoryRouter>,
    );

    expect(await screen.findByText("database is down")).toBeInTheDocument();
  });
});
