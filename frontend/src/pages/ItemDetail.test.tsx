import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { setToken } from "../api/client";
import { formatTime } from "../format";
import { ItemDetail } from "./ItemDetail";

/** The page's own words for the two live-pane failures. They are held here as
 * separate constants because the single thing this page must never do is blur
 * them together: "Plex is serving nothing" is a fact about the library, "Plex
 * could not be asked" is a fact about the connection, and an operator chasing
 * the first while the second is true wastes an afternoon. Two tests below
 * assert one each, so a component that collapsed them into one message would
 * redden one of the two whichever message it kept. */
const LIVE_ABSENT = "Plex is not serving any artwork of this kind.";
const LIVE_UNREACHABLE = "Plex could not be reached, so what it is serving is unknown.";

const FINGERPRINT = "9f2c1d3e4b5a69780c1122334455667788990aabbccddeeff00112233445566a";
const BADGE_FINGERPRINT = "1a2b3c4d5e6f70819876543210fedcba9876543210fedcba9876543210fedcba";

const MOVIE = {
  id: 3,
  title: "Ghostbusters",
  library: "Movies",
  kind: "movie",
  rating_key: "101",
  facts: {
    critic_rating: 8.4,
    audience_rating: 7.2,
    content_rating: "PG",
    genres: ["Comedy", "Fantasy"],
    studio: "Columbia Pictures",
    originally_available: "1984-06-08",
  },
  renders: [
    {
      art_kind: "poster",
      status: "rendered",
      fingerprint: FINGERPRINT,
      badge_fingerprint: BADGE_FINGERPRINT,
      upload_status: "uploaded",
      adopted: false,
      rendered_at: "2026-08-01T09:15:00Z",
      uploaded_at: "2026-08-02T18:40:00Z",
      provider: null,
    },
  ],
};

/** The same movie with both of its art kinds rendered, one of them from a
 * hand-placed override file. Listed poster-first on purpose: the panes are
 * specified as ordered by art kind, so a page that simply followed the array
 * would show them the other way round and redden the ordering assertion. */
const MOVIE_BOTH_KINDS = {
  ...MOVIE,
  renders: [
    { ...MOVIE.renders[0], art_kind: "poster", provider: "manual" },
    { ...MOVIE.renders[0], art_kind: "background", provider: "tmdb" },
  ],
};

/** An episode, whose only art kind is `title_card`. Asking this item for a
 * `poster` is a 404 from the API, so a page that hard-coded one would show two
 * empty panes for every episode in the library. */
const EPISODE = {
  id: 9,
  title: "Fly",
  library: "TV Shows",
  kind: "episode",
  rating_key: "909",
  facts: null,
  renders: [],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function imageBytes(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "image/webp" },
  });
}

type RouteMap = Record<string, () => Response | Promise<Response>>;

/** Only the paths a test names are answered; anything else throws, naming the
 * path. So a component that asked for the wrong art kind fails loudly rather
 * than silently receiving a plausible response. */
function stubFetch(routes: RouteMap) {
  // `init` is unread here but part of the signature on purpose: it is how a
  // test reaches the method a call was made with.
  const fetchMock = vi.fn(async (path: string, _init?: RequestInit) => {
    const route = routes[path];
    if (route === undefined) {
      throw new Error(`the page requested an unexpected path: ${path}`);
    }
    return route();
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The movie's routes, with either artwork endpoint replaceable. Both answer
 * bytes by default so that a test about one of them cannot pass because
 * everything failed the same way. */
function movieRoutes(overrides: RouteMap = {}): RouteMap {
  return {
    "/api/items/3": () => json(MOVIE),
    "/api/items/3/artwork/poster": () => imageBytes("base-image-bytes"),
    "/api/items/3/artwork/poster/live": () => imageBytes("live-image-bytes"),
    ...overrides,
  };
}

/** The two-art-kind movie's routes. Every pane's bytes differ, so a page that
 * pointed two panes at one endpoint cannot pass by accident. */
function bothKindRoutes(overrides: RouteMap = {}): RouteMap {
  return {
    "/api/items/3": () => json(MOVIE_BOTH_KINDS),
    "/api/items/3/artwork/poster": () => imageBytes("base-poster-bytes"),
    "/api/items/3/artwork/poster/live": () => imageBytes("live-poster-bytes"),
    "/api/items/3/artwork/background": () => imageBytes("base-background-bytes"),
    "/api/items/3/artwork/background/live": () => imageBytes("live-background-bytes"),
    ...overrides,
  };
}

/** Object URL -> the blob it was made from, so a pane can be proved to show
 * the bytes its own endpoint returned rather than the other pane's. */
const objectUrls = new Map<string, Blob>();
const realCreateObjectURL = URL.createObjectURL;
const realRevokeObjectURL = URL.revokeObjectURL;

async function renderItem(id = 3) {
  const view = render(
    <MemoryRouter initialEntries={[`/items/${id}`]}>
      <Routes>
        <Route path="/items/:itemId" element={<ItemDetail />} />
      </Routes>
    </MemoryRouter>,
  );
  // The panes exist only once the item itself has loaded, so waiting for a
  // caption waits for the first fetch. React then flushes passive effects
  // after the commit, so the panes' own requests are still in flight.
  // findAll rather than find: an item with two art kinds has two base panes,
  // and findBy throws on more than one match.
  await screen.findAllByText(/Base image/);
  await act(async () => {});
  return view;
}

/** One <section> per art kind, in the order the page laid them out. */
function kindSections(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>(".art-kind-panes")];
}

/** The <figure> whose caption starts with `caption`. By caption rather than by
 * position, so a page that swapped the two panes over would not quietly keep
 * passing. */
function pane(caption: string, within: ParentNode = document): HTMLElement {
  const found = [...within.querySelectorAll("figcaption")].find((node) =>
    (node.textContent ?? "").startsWith(caption),
  );
  if (found === undefined) throw new Error(`no pane captioned "${caption}"`);
  return found.closest("figure") as HTMLElement;
}

function renderRows(): HTMLElement[] {
  return [...document.querySelectorAll<HTMLElement>(".render-table tbody tr")];
}

/** The row whose first cell is this art kind. */
function renderRow(artKind: string): HTMLElement {
  const found = renderRows().find(
    (row) => row.querySelector("td")?.textContent === artKind,
  );
  if (found === undefined) throw new Error(`no render row for "${artKind}"`);
  return found;
}

function noteIn(element: HTMLElement): string {
  const note = element.querySelector(".art-note");
  if (note === null) throw new Error("the pane shows no note");
  return note.textContent ?? "";
}

async function bytesShownBy(element: HTMLElement): Promise<string> {
  const image = element.querySelector("img");
  if (image === null) throw new Error("the pane shows no image");
  const blob = objectUrls.get(image.getAttribute("src") ?? "");
  if (blob === undefined) throw new Error("the image's src is not an object URL");
  return await blob.text();
}

beforeEach(() => {
  setToken(null);
  objectUrls.clear();
  // jsdom implements neither, and `vi.stubGlobal` cannot reach them: they are
  // statics on `URL`, not globals of their own.
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

describe("ItemDetail", () => {
  it("shows the base image and the live image, each from its own endpoint", async () => {
    stubFetch(movieRoutes());

    await renderItem();

    // Not "an <img> exists": the object URL is resolved back to its blob and
    // the bytes compared, so a page that showed the base image in both panes
    // fails here rather than looking right.
    expect(await bytesShownBy(pane("Base image"))).toBe("base-image-bytes");
    expect(await bytesShownBy(pane("Live in Plex"))).toBe("live-image-bytes");
  });

  it("says Plex is serving nothing, with no broken image, when live 404s", async () => {
    stubFetch(
      movieRoutes({
        "/api/items/3/artwork/poster/live": () =>
          json({ detail: "Plex has no artwork for this item" }, 404),
      }),
    );

    await renderItem();

    const live = pane("Live in Plex");
    // An <img> with a src that cannot load is exactly the broken-image icon
    // this has to avoid.
    expect(live.querySelector("img")).toBeNull();
    expect(noteIn(live)).toBe(LIVE_ABSENT);
    // The base pane still has its image, so this is the live endpoint's 404
    // being reported and not the whole page having failed.
    expect(await bytesShownBy(pane("Base image"))).toBe("base-image-bytes");
  });

  it("says Plex is unreachable, in different words, when live 503s", async () => {
    stubFetch(
      movieRoutes({
        "/api/items/3/artwork/poster/live": () =>
          json({ detail: "this instance is not connected to Plex" }, 503),
      }),
    );

    await renderItem();

    const live = pane("Live in Plex");
    expect(live.querySelector("img")).toBeNull();
    expect(noteIn(live)).toBe(LIVE_UNREACHABLE);
    // The point of the test: 503 must not read as "nothing is uploaded".
    expect(noteIn(live)).not.toBe(LIVE_ABSENT);
    // The server's own explanation is shown too -- but underneath the page's
    // own sentence, so the distinction does not depend on what Plex said.
    expect(live.querySelector(".art-detail")?.textContent).toBe(
      "this instance is not connected to Plex",
    );
    expect(await bytesShownBy(pane("Base image"))).toBe("base-image-bytes");
  });

  it("falls back to the note, not a broken image, when the bytes will not decode", async () => {
    // The server 404s an empty Plex body, but non-empty corrupt bytes reach
    // the <img> as a blob that fails to decode. Left alone that paints the
    // browser's broken-image icon -- the one thing these panes promise never
    // to show.
    stubFetch(movieRoutes());
    await renderItem();

    for (const [caption, note] of [
      ["Base image", "The base image could not be loaded."],
      ["Live in Plex", "The live image could not be loaded."],
    ] as const) {
      const box = pane(caption);
      const image = box.querySelector("img");
      expect(image).not.toBeNull();
      const source = image!.getAttribute("src");

      fireEvent.error(image!);

      expect(box.querySelector("img")).toBeNull();
      expect(noteIn(box)).toBe(note);
      // Revoked, not merely dropped: an undecodable blob is still decoded
      // bytes held by the document until someone lets go of it.
      expect(URL.revokeObjectURL).toHaveBeenCalledWith(source);
    }
  });

  it("says nothing has been rendered when the base image 404s", async () => {
    stubFetch(
      movieRoutes({
        "/api/items/3/artwork/poster": () => json({ detail: "artwork not found" }, 404),
      }),
    );

    await renderItem();

    const base = pane("Base image");
    expect(base.querySelector("img")).toBeNull();
    expect(noteIn(base)).toBe("Nothing has been rendered for this item yet.");
    expect(await bytesShownBy(pane("Live in Plex"))).toBe("live-image-bytes");
  });

  it("asks for the art kind the item actually has, not poster", async () => {
    // No /api/items/9/artwork/poster route exists, so a hard-coded "poster"
    // hits the stub's throw instead of quietly getting an image.
    const fetchMock = stubFetch({
      "/api/items/9": () => json(EPISODE),
      "/api/items/9/artwork/title_card": () => imageBytes("title-card-bytes"),
      "/api/items/9/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(9);

    expect(
      fetchMock.mock.calls
        .map((call) => call[0] as string)
        .filter((path) => path.includes("/artwork/")),
    ).toEqual([
      "/api/items/9/artwork/title_card",
      "/api/items/9/artwork/title_card/live",
    ]);
    expect(await bytesShownBy(pane("Base image"))).toBe("title-card-bytes");
  });

  it("shapes the pane box for the art kind: 2:3 for a poster, 16:9 for a title card", async () => {
    // The box's aspect ratio comes from item.css keyed on this attribute; a
    // pane that hard-coded a poster's 2:3 would letterbox every episode's
    // 16:9 title card at a third of a tall grey box, in both panes, on a page
    // whose whole point is comparing the two images by eye.
    stubFetch(movieRoutes());
    const view = await renderItem();

    expect(pane("Base image")).toHaveAttribute("data-ratio", "poster");
    expect(pane("Live in Plex")).toHaveAttribute("data-ratio", "poster");

    view.unmount();
    stubFetch({
      "/api/items/9": () => json(EPISODE),
      "/api/items/9/artwork/title_card": () => imageBytes("title-card-bytes"),
      "/api/items/9/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });
    await renderItem(9);

    expect(pane("Base image")).toHaveAttribute("data-ratio", "wide");
    expect(pane("Live in Plex")).toHaveAttribute("data-ratio", "wide");
  });

  it("shows the facts and the render history, fingerprints truncated", async () => {
    stubFetch(movieRoutes());

    await renderItem();

    const facts = [...document.querySelectorAll(".fact-list dd")].map(
      (node) => node.textContent,
    );
    expect(facts).toEqual([
      "8.4",
      "7.2",
      "PG",
      "Columbia Pictures",
      "Comedy, Fantasy",
      "1984-06-08",
    ]);

    const row = document.querySelector(".render-table tbody tr");
    expect(row).not.toBeNull();
    // The whole row at once, compared exactly: `toHaveTextContent` is a
    // substring match, so a badge fingerprint rendered in the fingerprint
    // column would satisfy a looser assertion.
    expect([...row!.querySelectorAll("td")].map((cell) => cell.textContent)).toEqual([
      "poster",
      "rendered",
      // A null provider is a row nothing has rendered a source for yet, not
      // the string "null".
      "—",
      `${FINGERPRINT.slice(0, 12)}…`,
      `${BADGE_FINGERPRINT.slice(0, 12)}…`,
      "uploaded",
      formatTime("2026-08-01T09:15:00Z"),
      formatTime("2026-08-02T18:40:00Z"),
    ]);

    // Truncated on screen, whole on hover.
    expect(
      [...row!.querySelectorAll(".fingerprint")].map((node) =>
        node.getAttribute("title"),
      ),
    ).toEqual([FINGERPRINT, BADGE_FINGERPRINT]);
  });

  it("re-runs by posting to /reprocess and reports the job the server queued", async () => {
    const fetchMock = stubFetch(
      // 412 rather than a small number on purpose: a substring assertion for
      // "12" would pass against it, so the test below compares exactly.
      movieRoutes({
        "/api/items/3/reprocess": () => json({ queued: true, job_id: 412 }),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));

    await waitFor(() =>
      expect(document.querySelector(".item-outcome")?.textContent).toBe(
        "Queued as job #412.",
      ),
    );

    const post = fetchMock.mock.calls.find((call) => call[0] === "/api/items/3/reprocess");
    expect(post).toBeDefined();
    expect(post![1]?.method).toBe("POST");
  });

  it("reports a de-duplicated re-run instead of claiming a job was queued", async () => {
    // The endpoint answers this when an identical job is already pending. A
    // page that reported success optimistically would say the opposite of
    // what happened.
    stubFetch(
      movieRoutes({
        "/api/items/3/reprocess": () => json({ queued: false, job_id: null }),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));

    await waitFor(() =>
      expect(document.querySelector(".item-outcome")?.textContent).toBe(
        "Already queued — nothing new was added.",
      ),
    );
  });

  it("reports a failed re-run instead of claiming anything was queued", async () => {
    stubFetch(
      movieRoutes({
        "/api/items/3/reprocess": () => json({ detail: "the queue is unavailable" }, 500),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));

    await waitFor(() =>
      expect(document.querySelector(".page-error")?.textContent).toBe(
        "the queue is unavailable",
      ),
    );
    // An error, not an outcome: a page showing both would be saying the
    // re-run failed and was queued at once.
    expect(document.querySelector(".item-outcome")).toBeNull();
  });

  it("shows a labelled base+live pair for every art kind the item has rendered", async () => {
    // A movie has both a poster and a background. Showing only the poster hid
    // half of what this page exists to compare, and there was no way to see a
    // background at all.
    stubFetch(bothKindRoutes());

    await renderItem();

    const sections = kindSections();
    expect(
      sections.map((section) => section.querySelector(".art-kind-label")?.textContent),
    ).toEqual(["background", "poster"]);

    // Each pane resolved back to the bytes its own endpoint returned, so a
    // page that pointed both pairs at one art kind fails here.
    expect(await bytesShownBy(pane("Base image", sections[0]))).toBe(
      "base-background-bytes",
    );
    expect(await bytesShownBy(pane("Live in Plex", sections[0]))).toBe(
      "live-background-bytes",
    );
    expect(await bytesShownBy(pane("Base image", sections[1]))).toBe("base-poster-bytes");
    expect(await bytesShownBy(pane("Live in Plex", sections[1]))).toBe(
      "live-poster-bytes",
    );

    // Per pair, not per page: a 16:9 background and a 2:3 poster on one screen
    // is exactly the case a single shared ratio gets wrong.
    expect(pane("Base image", sections[0])).toHaveAttribute("data-ratio", "wide");
    expect(pane("Live in Plex", sections[0])).toHaveAttribute("data-ratio", "wide");
    expect(pane("Base image", sections[1])).toHaveAttribute("data-ratio", "poster");
    expect(pane("Live in Plex", sections[1])).toHaveAttribute("data-ratio", "poster");
  });

  it("falls back to the item's own art kind when nothing has been rendered", async () => {
    // EPISODE has an empty renders array, so there is no art kind to take from
    // it -- and a page that showed no panes at all would leave the operator
    // unable to see whether Plex is serving something this project never made.
    stubFetch({
      "/api/items/9": () => json(EPISODE),
      "/api/items/9/artwork/title_card": () => imageBytes("title-card-bytes"),
      "/api/items/9/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(9);

    const sections = kindSections();
    expect(
      sections.map((section) => section.querySelector(".art-kind-label")?.textContent),
    ).toEqual(["title_card"]);
    expect(await bytesShownBy(pane("Base image", sections[0]))).toBe("title-card-bytes");
  });

  it("offers Clear override only on a render row whose provider is manual", async () => {
    stubFetch(bothKindRoutes());

    await renderItem();

    // Both providers are shown; only one of them is an override.
    expect(renderRow("poster").querySelectorAll("td")[2].textContent).toContain("manual");
    expect(renderRow("background").querySelectorAll("td")[2].textContent).toBe("tmdb");

    const buttons = screen.getAllByRole("button", { name: "Clear override" });
    expect(buttons).toHaveLength(1);
    // In the manual row, not merely somewhere on the page: a button rendered
    // once per table would still satisfy a bare count.
    expect(renderRow("poster").contains(buttons[0])).toBe(true);
    expect(renderRow("background").querySelector("button")).toBeNull();
  });

  it("clears an override, re-fetches the item and reports the queued re-render", async () => {
    let detailCalls = 0;
    const fetchMock = stubFetch(
      bothKindRoutes({
        "/api/items/3": () => {
          detailCalls += 1;
          return json(MOVIE_BOTH_KINDS);
        },
        "/api/items/3/renders/poster/clear-override": () =>
          json({ status: "cleared", queued: true }),
      }),
    );

    await renderItem();
    expect(detailCalls).toBe(1);

    fireEvent.click(screen.getByRole("button", { name: "Clear override" }));

    await waitFor(() => expect(document.querySelector(".render-note")).not.toBeNull());

    const post = fetchMock.mock.calls.find(
      (call) => call[0] === "/api/items/3/renders/poster/clear-override",
    );
    expect(post).toBeDefined();
    expect(post![1]?.method).toBe("POST");

    // The row's fingerprints are cleared server-side, so the table on screen is
    // stale until the item is read again.
    expect(detailCalls).toBe(2);

    const note = document.querySelector(".render-note")!.textContent ?? "";
    // The endpoint renames the file to .disabled. Telling an operator their own
    // artwork was deleted would be false, and restoring it is a rename back.
    expect(note).toContain("disabled");
    expect(note).not.toContain("deleted");
    // What the server said happened, not what the click hoped for.
    expect(note).toContain("queued");

    // Inline against the row it belongs to, not a page-level banner: with two
    // render rows a floating message says nothing about which one it means.
    expect(
      document.querySelector(".render-note")!.closest("tr")!.previousElementSibling,
    ).toBe(renderRow("poster"));
  });

  it("says nothing new was queued when the server says nothing was", async () => {
    stubFetch(
      bothKindRoutes({
        "/api/items/3/renders/poster/clear-override": () =>
          json({ status: "cleared", queued: false }),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Clear override" }));

    await waitFor(() => expect(document.querySelector(".render-note")).not.toBeNull());
    const note = document.querySelector(".render-note")!.textContent ?? "";
    expect(note).toContain("disabled");
    // The reprocess enqueue de-duplicates, so "queued a re-render" would be the
    // opposite of what the response reported.
    expect(note).not.toMatch(/queued a re-render/);
  });

  it("surfaces a 409 beside the row instead of claiming the override was cleared", async () => {
    let detailCalls = 0;
    stubFetch(
      bothKindRoutes({
        "/api/items/3": () => {
          detailCalls += 1;
          return json(MOVIE_BOTH_KINDS);
        },
        "/api/items/3/renders/poster/clear-override": () =>
          json({ detail: "no manual override for this art kind" }, 409),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Clear override" }));

    await waitFor(() =>
      expect(document.querySelector(".render-error")?.textContent).toBe(
        "no manual override for this art kind",
      ),
    );
    // An error, not a success note: a page showing both would be saying the
    // clear failed and worked at once.
    expect(document.querySelector(".render-note")).toBeNull();
    // Nothing changed on the server, so nothing needed re-reading.
    expect(detailCalls).toBe(1);
    expect(
      document.querySelector(".render-error")!.closest("tr")!.previousElementSibling,
    ).toBe(renderRow("poster"));
  });

  it("surfaces the 503 rename failure verbatim, mount path and all", async () => {
    // The detail is the OS error, which names the absolute path on the mount.
    // Behind auth, that is the only thing that tells an operator which file to
    // go and look at -- a generic "could not clear" would strand them.
    const detail =
      "[Errno 30] Read-only file system: '/manualassets/Movies/Ghostbusters (1984)/poster.png'";
    stubFetch(
      bothKindRoutes({
        "/api/items/3/renders/poster/clear-override": () => json({ detail }, 503),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Clear override" }));

    await waitFor(() =>
      expect(document.querySelector(".render-error")?.textContent).toBe(detail),
    );
  });

  it("disables Clear override while its own request is in flight", async () => {
    // The endpoint is deliberately non-idempotent: a second click during the
    // first call 409s on a file that has already been renamed, which would
    // report a failure for a clear that actually succeeded.
    let release!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    stubFetch(
      bothKindRoutes({
        "/api/items/3/renders/poster/clear-override": () => pending,
      }),
    );

    await renderItem();

    expect(screen.getByRole("button", { name: "Clear override" })).not.toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Clear override" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Clear override" })).toBeDisabled(),
    );

    await act(async () => {
      release(json({ status: "cleared", queued: true }));
    });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Clear override" })).not.toBeDisabled(),
    );
  });

  it("is what /items/:itemId reaches in the real router", async () => {
    // Without this the page can be complete and unreachable: App.tsx shipped a
    // placeholder element on this route, and a suite that only ever renders
    // ItemDetail directly stays green while every tile click shows it.
    stubFetch(movieRoutes());
    setToken("a-session-token");
    window.history.pushState({}, "", "/items/3");

    render(<App />);

    const heading = await screen.findByRole("heading", {
      level: 1,
      name: "Ghostbusters",
    });
    expect(heading).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Renders" })).toBeInTheDocument();
    expect(screen.queryByText(/not built yet/)).toBeNull();

    await act(async () => {});
    window.history.pushState({}, "", "/");
  });
});
