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
    },
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

type RouteMap = Record<string, () => Response>;

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
  await screen.findByText(/Base image/);
  await act(async () => {});
  return view;
}

/** The <figure> whose caption starts with `caption`. By caption rather than by
 * position, so a page that swapped the two panes over would not quietly keep
 * passing. */
function pane(caption: string): HTMLElement {
  const found = [...document.querySelectorAll("figcaption")].find((node) =>
    (node.textContent ?? "").startsWith(caption),
  );
  if (found === undefined) throw new Error(`no pane captioned "${caption}"`);
  return found.closest("figure") as HTMLElement;
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
