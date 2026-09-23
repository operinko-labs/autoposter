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
      refs: { plex: "101" },
      // Two kinds with different statuses on purpose: the status filter
      // matches ANY render row, so a tile must caption every kind rather
      // than letting the poster's status stand for the whole item.
      render_status: { poster: "rendered", background: "no_art" },
    },
    {
      id: 7,
      title: "Arcane",
      library: "TV Shows",
      kind: "show",
      refs: { plex: "202" },
      render_status: {},
    },
  ],
};

/** Only item 3 has artwork on disk. Item 7 is an ordinary un-rendered item,
 * which the endpoint answers with a 404. */
const ARTWORK: Record<string, string> = {
  "3/poster": "jpeg-bytes-for-ghostbusters",
};

/** A tile's artwork request. `?w=` is required: a tile asking for the full
 * 2000x3000 render is the thing this suite exists to catch, so such a request
 * falls through to the stub's "unexpected path" throw. 320/640 mirror
 * ThumbWidth in src/autoposter/api/thumbs.py. */
const ARTWORK_PATH = /^\/api\/items\/(\d+)\/artwork\/([a-z_]+)\?w=(320|640)$/;

/** Must equal SEARCH_DEBOUNCE_MS in Library.tsx. Held here as a number the
 * tests do arithmetic on -- one tick short of it must still be silent. */
const SEARCH_DEBOUNCE_MS = 300;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch() {
  // `init` is unread here but part of the signature on purpose: it is how a
  // test reaches the cache mode a call was made with.
  const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
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
      "/api/items/3/artwork/poster?w=320",
      "/api/items/7/artwork/poster?w=320",
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

  it.each([
    [1, "320"],
    [1.25, "320"],
    [1.5, "640"],
    [2, "640"],
  ])(
    "asks for a thumbnail sized for a devicePixelRatio of %s (w=%s)",
    async (ratio, width) => {
      // Restored after each test by `unstubGlobals: true` in vite.config.ts.
      vi.stubGlobal("devicePixelRatio", ratio);
      const fetchMock = stubFetch();

      await renderLibrary();

      expect(pathsMatching(fetchMock, (path) => ARTWORK_PATH.test(path))).toEqual([
        `/api/items/3/artwork/poster?w=${width}`,
        `/api/items/7/artwork/poster?w=${width}`,
      ]);
    },
  );

  it("leaves tile caching to the browser rather than forcing a revalidation", async () => {
    // The server marks artwork fresh for five minutes; the grid is where that
    // is meant to save requests. Only the item page opts out.
    const fetchMock = stubFetch();

    await renderLibrary();

    const artworkCalls = fetchMock.mock.calls.filter(([path]) => ARTWORK_PATH.test(path));
    expect(artworkCalls).toHaveLength(2);
    for (const [, init] of artworkCalls) expect(init?.cache).toBeUndefined();
  });

  it("captions each tile with one chip per art kind, not a single status", async () => {
    stubFetch();

    await renderLibrary();

    // Exact text per chip, in the order the API sent the kinds. A substring
    // match would let "poster: rendered" alone pass while the background's
    // no_art -- the very status a filter may have matched on -- went unshown.
    const chips = [...tileFor("Ghostbusters").querySelectorAll(".tile-chip")].map(
      (chip) => chip.textContent,
    );
    expect(chips).toEqual(["poster: rendered", "background: no_art"]);

    // The old single-status caption is gone: the subtitle is the library
    // alone, with no status folded into it.
    expect(tileFor("Ghostbusters").querySelector(".tile-sub")!.textContent).toBe(
      "Movies",
    );

    // A kind absent from render_status gets no chip at all -- Arcane has an
    // empty render_status, so its tile carries none.
    expect(tileFor("Arcane").querySelectorAll(".tile-chip")).toHaveLength(0);
    expect(tileFor("Arcane").querySelector(".tile-sub")!.textContent).toBe(
      "TV Shows",
    );
  });

  it("highlights the chip matching the active status filter", async () => {
    stubFetch();

    await renderLibrary();

    // No filter selected: nothing is highlighted.
    expect(tileFor("Ghostbusters").querySelector(".tile-chip-match")).toBeNull();

    fireEvent.change(screen.getByLabelText("Status"), {
      target: { value: "no_art" },
    });

    // The filter matches ANY kind's row, so the highlighted chip is what
    // tells the user why a tile whose poster reads "rendered" is in a
    // no_art result at all.
    await waitFor(() => {
      const match = tileFor("Ghostbusters").querySelector(".tile-chip-match");
      expect(match).not.toBeNull();
      expect(match!.textContent).toBe("background: no_art");
    });
    // Only the matching chip is emphasised.
    expect(
      tileFor("Ghostbusters").querySelectorAll(".tile-chip-match"),
    ).toHaveLength(1);
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

  it("waits out the debounce, then searches from offset 0 rather than the current page", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = stubFetch();
      render(
        <MemoryRouter>
          <Library />
        </MemoryRouter>,
      );
      // Fake timers do not touch microtasks, so the mount fetches still settle.
      await act(async () => {});

      const listings = () =>
        pathsMatching(fetchMock, (path) => path.startsWith("/api/items?"));

      expect(listings()).toEqual(["/api/items?limit=48&offset=0"]);

      // Move off the first page BEFORE typing. Without this a search that kept
      // the offset would be indistinguishable from one that reset it, because
      // the offset was already 0.
      fireEvent.click(screen.getByRole("button", { name: "Next" }));
      await act(async () => {});
      expect(listings()).toEqual([
        "/api/items?limit=48&offset=0",
        "/api/items?limit=48&offset=48",
      ]);

      const box = screen.getByLabelText("Search");
      fireEvent.change(box, { target: { value: "gho" } });
      await act(async () => {});
      fireEvent.change(box, { target: { value: "ghost" } });
      await act(async () => {});

      // Nothing has been asked for yet. A page that queried per keystroke would
      // already have issued two listings against a 15,000-row table.
      expect(listings()).toHaveLength(2);

      // One tick short: still silent. This is what makes the delay a real
      // debounce rather than a timer that happens to be somewhere in the code.
      await act(async () => {
        vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS - 1);
      });
      expect(listings()).toHaveLength(2);

      await act(async () => {
        vi.advanceTimersByTime(1);
      });
      await act(async () => {});

      // Exactly one listing for two keystrokes, carrying the final word, and
      // from offset 0 -- page 2 of a search that has one page is answered with
      // an empty list, which reads as "nothing matched".
      expect(listings()).toEqual([
        "/api/items?limit=48&offset=0",
        "/api/items?limit=48&offset=48",
        "/api/items?limit=48&offset=0&search=ghost",
      ]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("sends no search param at all once the box is emptied", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = stubFetch();
      render(
        <MemoryRouter>
          <Library />
        </MemoryRouter>,
      );
      await act(async () => {});

      const listings = () =>
        pathsMatching(fetchMock, (path) => path.startsWith("/api/items?"));

      const box = screen.getByLabelText("Search");
      fireEvent.change(box, { target: { value: "ghost" } });
      await act(async () => {
        vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
      });
      await act(async () => {});
      expect(listings()).toEqual([
        "/api/items?limit=48&offset=0",
        "/api/items?limit=48&offset=0&search=ghost",
      ]);

      fireEvent.change(box, { target: { value: "" } });
      await act(async () => {
        vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
      });
      await act(async () => {});

      // `search=` with nothing after it is not the same request as no search:
      // it is a filter the backend would still evaluate.
      expect(listings()).toEqual([
        "/api/items?limit=48&offset=0",
        "/api/items?limit=48&offset=0&search=ghost",
        "/api/items?limit=48&offset=0",
      ]);
    } finally {
      vi.useRealTimers();
    }
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
    // path.includes rather than ARTWORK_PATH: the latter now requires `?w=`,
    // so a premature request missing it would slip past ARTWORK_PATH and make
    // this assertion vacuous.
    expect(pathsMatching(fetchMock, (path) => path.includes("/artwork/"))).toEqual([]);
  });

  it("reports a failed listing instead of an endless spinner", async () => {
    // Only the listing fails. Failing every fetch would let a component that
    // surfaced only a filters error, with the grid spinning forever, pass.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path === "/api/items/filters") return json(FILTERS);
        if (path.startsWith("/api/items?")) return json({ detail: "database is down" }, 500);
        throw new Error(`the page requested an unexpected path: ${path}`);
      }),
    );

    render(
      <MemoryRouter>
        <Library />
      </MemoryRouter>,
    );

    expect(await screen.findByText("database is down")).toBeInTheDocument();
  });

  it("keeps reporting a filters failure after the listing succeeds", async () => {
    // The two effects race on mount, and the items effect clears its own error
    // on success -- a shared error state would let that success erase the
    // filters failure, leaving three inexplicably empty dropdowns.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path === "/api/items/filters") return json({ detail: "filters are down" }, 500);
        if (path.startsWith("/api/items?")) return json(ITEMS);
        throw new Error(`the page requested an unexpected path: ${path}`);
      }),
    );

    render(
      <MemoryRouter>
        <Library />
      </MemoryRouter>,
    );

    // The listing itself succeeded: the grid is here...
    await screen.findByRole("link", { name: /Ghostbusters/ });
    // ...and the filters failure is still on screen, not erased by it.
    expect(
      await screen.findByText("Filters are unavailable: filters are down"),
    ).toBeInTheDocument();
  });

  it("disables the pager buttons at the edges of the result", async () => {
    // Everything fits on one page, so neither button has anywhere to go.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path === "/api/items/filters") return json(FILTERS);
        if (path.startsWith("/api/items?")) {
          return json({ total: 2, items: ITEMS.items });
        }
        throw new Error(`the page requested an unexpected path: ${path}`);
      }),
    );

    render(
      <MemoryRouter>
        <Library />
      </MemoryRouter>,
    );
    await screen.findByRole("link", { name: /Ghostbusters/ });

    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });

  it("composes the search with the active filters in one listing URL", async () => {
    // Roadmap row 109: the listing effect builds ONE URLSearchParams from
    // library/kind/status/search together. The code is correct today; this
    // pins it so a dep-list regression in the effect -- a filter or the
    // debounced search silently dropped from the request -- goes red instead
    // of unseen.
    vi.useFakeTimers();
    try {
      const fetchMock = stubFetch();
      render(
        <MemoryRouter>
          <Library />
        </MemoryRouter>,
      );
      await act(async () => {});

      fireEvent.change(screen.getByLabelText("Library"), {
        target: { value: "Movies" },
      });
      await act(async () => {});
      fireEvent.change(screen.getByLabelText("Kind"), {
        target: { value: "movie" },
      });
      await act(async () => {});
      fireEvent.change(screen.getByLabelText("Status"), {
        target: { value: "rendered" },
      });
      await act(async () => {});
      fireEvent.change(screen.getByLabelText("Search"), {
        target: { value: "ghost" },
      });
      await act(async () => {
        vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
      });
      await act(async () => {});

      const listings = pathsMatching(fetchMock, (path) =>
        path.startsWith("/api/items?"),
      );
      // The LAST listing carries everything at once, in the effect's own
      // build order -- limit/offset first, then the three selects, then the
      // debounced search.
      expect(listings.at(-1)).toBe(
        "/api/items?limit=48&offset=0&library=Movies&kind=movie&status=rendered&search=ghost",
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
