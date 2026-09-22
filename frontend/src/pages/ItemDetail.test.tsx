import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../App";
import { TMDB_NOTICE, TVDB_NOTICE } from "../ProviderAttribution";
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
  refs: { plex: "101" },
  season_number: null,
  episode_number: null,
  parent: null,
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
      source_url: null,
      textless: null,
    },
  ],
  servers: [],
};

/** The absolute path a manual override stamps into `source_url`. It is a path
 * on the operator's mount, not a URL: a page that turned it into an href or an
 * <img src> would emit `file:`-ish nonsense the browser cannot fetch, and on a
 * page that hot-links provider thumbnails the difference is one line of code
 * apart. Asserted below to be plain text. */
const OVERRIDE_PATH = "/manualassets/Movies/Ghostbusters (1984)/poster.jpg";

/** The same movie with both of its art kinds rendered, one of them from a
 * hand-placed override file. Listed poster-first on purpose: the panes are
 * specified as ordered by art kind, so a page that simply followed the array
 * would show them the other way round and redden the ordering assertion. */
const MOVIE_BOTH_KINDS = {
  ...MOVIE,
  renders: [
    {
      ...MOVIE.renders[0],
      art_kind: "poster",
      provider: "manual",
      source_url: OVERRIDE_PATH,
      // Null under an override: there was no candidate to ask.
      textless: null,
    },
    {
      ...MOVIE.renders[0],
      art_kind: "background",
      provider: "tmdb",
      source_url: "https://image.tmdb.org/t/p/original/bg.jpg",
      textless: true,
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
  refs: { plex: "909" },
  season_number: null,
  episode_number: null,
  parent: null,
  facts: null,
  renders: [],
  servers: [],
};

/** The same episode, but with the show the API resolves for it -- an
 * episode's parent_id points at its season row, whose own parent_id points
 * at the show (src/autoposter/api/routes.py's item_detail), so the response
 * carries the show two hops up as `parent`, plus this row's own
 * season/episode numbers. */
const EPISODE_WITH_PARENT = {
  ...EPISODE,
  id: 154245,
  title: "Episode 26",
  refs: { plex: "154245" },
  season_number: 2,
  episode_number: 26,
  parent: { id: 42, title: "Firefly" },
};

/** A season, analogous to EPISODE_WITH_PARENT: its parent_id points straight
 * at the show. */
const SEASON_WITH_PARENT = {
  id: 88,
  title: "Season 2",
  library: "TV Shows",
  kind: "season",
  refs: { plex: "8080" },
  season_number: 2,
  episode_number: null,
  parent: { id: 42, title: "Firefly" },
  facts: null,
  renders: [],
  servers: [],
};

/** Two candidates from two providers, one provider having failed.
 *
 * Every field differs between the two tiles -- provider, language, dimensions,
 * textlessness, and both URLs -- so a panel that rendered one tile's data twice,
 * or read `thumb_url` where it meant `url`, cannot pass by coincidence. The
 * thumb URLs are deliberately NOT the full-size ones: the pick body must carry
 * `url`, and a tile that posted what it displayed would be indistinguishable
 * from a correct one if the two strings were equal.
 */
const TMDB_URL = "https://image.tmdb.org/t/p/original/aaaaaa.jpg";
const TMDB_THUMB = "https://image.tmdb.org/t/p/w342/aaaaaa.jpg";
const TVDB_URL = "https://artworks.thetvdb.com/banners/posters/bbbbbb.jpg";

const CANDIDATES = {
  candidates: [
    {
      provider: "tmdb",
      url: TMDB_URL,
      thumb_url: TMDB_THUMB,
      language: "en",
      width: 2000,
      height: 3000,
      score: 9.1,
      includes_text: null,
    },
    {
      provider: "tvdb",
      url: TVDB_URL,
      thumb_url: TVDB_URL,
      language: "fi",
      width: 1000,
      height: 1500,
      score: 4.2,
      includes_text: false,
    },
  ],
  // The exception's class name, which is what the endpoint sends -- deliberately
  // not a friendly sentence, so the page has to say which provider and what.
  errors: { fanart: "ReadTimeout" },
  current: { source_url: TVDB_URL, provider: "tvdb" },
};

/** A logo grid as TMDB actually serves one: an SVG beside a raster.
 *
 * TMDB's `logos` array carries `.svg` file paths and nothing filters them
 * (providers/tmdb.py); the browse copies every candidate through, and
 * `thumb_url` (api/candidates.py) rewrites only the `/t/p/original` prefix, so
 * the `.svg` segment survives into BOTH urls and the tile renders it in a plain
 * <img> exactly like a jpeg. The response carries no per-candidate pickability
 * field and no content type, so the grid has only the URL to go on -- which is
 * the whole of what these two tiles pin.
 *
 * `current` is null because a logo has no render row: browse_candidates skips
 * the lookup entirely for that art kind. */
const TMDB_SVG_URL = "https://image.tmdb.org/t/p/original/cccccc.svg";
const TMDB_SVG_THUMB = "https://image.tmdb.org/t/p/w342/cccccc.svg";
const TMDB_LOGO_URL = "https://image.tmdb.org/t/p/original/dddddd.png";
const TMDB_LOGO_THUMB = "https://image.tmdb.org/t/p/w342/dddddd.png";

const LOGO_CANDIDATES = {
  candidates: [
    {
      provider: "tmdb",
      url: TMDB_SVG_URL,
      thumb_url: TMDB_SVG_THUMB,
      language: "en",
      width: null,
      height: null,
      score: 8.1,
      includes_text: null,
    },
    {
      provider: "tmdb",
      url: TMDB_LOGO_URL,
      thumb_url: TMDB_LOGO_THUMB,
      language: "en",
      width: 1600,
      height: 400,
      score: 5.4,
      includes_text: null,
    },
  ],
  errors: {},
  current: null,
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
    "/api/items/3/artwork/poster?w=640": () => imageBytes("base-image-bytes"),
    "/api/items/3/artwork/poster/live": () => imageBytes("live-image-bytes"),
    ...overrides,
  };
}

/** The two-art-kind movie's routes. Every pane's bytes differ, so a page that
 * pointed two panes at one endpoint cannot pass by accident. */
function bothKindRoutes(overrides: RouteMap = {}): RouteMap {
  return {
    "/api/items/3": () => json(MOVIE_BOTH_KINDS),
    "/api/items/3/artwork/poster?w=640": () => imageBytes("base-poster-bytes"),
    "/api/items/3/artwork/poster/live": () => imageBytes("live-poster-bytes"),
    "/api/items/3/artwork/background?w=640": () => imageBytes("base-background-bytes"),
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

/** The open candidate panel, or a throw naming what was expected. */
function candidatePanel(): HTMLElement {
  const panel = document.querySelector<HTMLElement>(".candidate-panel");
  if (panel === null) throw new Error("no candidate panel is open");
  return panel;
}

function tiles(): HTMLElement[] {
  return [...candidatePanel().querySelectorAll<HTMLElement>(".candidate-tile")];
}

/** Opens the panel a button label names and waits for its tiles. */
async function openPanel(name: string) {
  fireEvent.click(screen.getByRole("button", { name }));
  await waitFor(() => expect(tiles().length).toBeGreaterThan(0));
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
        "/api/items/3/artwork/poster?w=640": () => json({ detail: "artwork not found" }, 404),
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
      "/api/items/9/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
      "/api/items/9/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(9);

    expect(
      fetchMock.mock.calls
        .map((call) => call[0] as string)
        .filter((path) => path.includes("/artwork/")),
    ).toEqual([
      "/api/items/9/artwork/title_card?w=640",
      "/api/items/9/artwork/title_card/live",
    ]);
    expect(await bytesShownBy(pane("Base image"))).toBe("title-card-bytes");
  });

  it("asks for the stored image at w=640 with no-cache, and leaves the live pane alone", async () => {
    // no-cache because the server lets a browser reuse artwork for five
    // minutes without asking; this page is where an operator looks at a
    // render they have just made, so it must always revalidate. The live
    // pane is Plex's own no-store response and is not touched.
    const fetchMock = stubFetch(movieRoutes());

    await renderItem();

    const artworkCalls = fetchMock.mock.calls.filter(([path]) => path.includes("/artwork/"));
    expect(artworkCalls.map(([path]) => path)).toEqual([
      "/api/items/3/artwork/poster?w=640",
      "/api/items/3/artwork/poster/live",
    ]);
    const [[, baseInit], [, liveInit]] = artworkCalls;
    expect(baseInit?.cache).toBe("no-cache");
    expect(liveInit?.cache).toBeUndefined();
    expect(await bytesShownBy(pane("Base image"))).toBe("base-image-bytes");
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
      "/api/items/9/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
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
      // Same for a null source_url: an em dash, not "null" and not an empty
      // cell that reads as a rendering failure.
      "—",
      `${FINGERPRINT.slice(0, 12)}…`,
      `${BADGE_FINGERPRINT.slice(0, 12)}…`,
      "uploaded",
      // No deliveries on this fixture's render row.
      "—",
      formatTime("2026-08-01T09:15:00Z"),
      formatTime("2026-08-02T18:40:00Z"),
    ]);

    // Truncated on screen, whole on hover.
    expect(
      [...row!.querySelectorAll(".fingerprint")].map((node) =>
        node.getAttribute("title"),
      ),
    ).toEqual([FINGERPRINT, BADGE_FINGERPRINT]);

    // Ten nowrapped columns cannot fit a phone, so the table carries its own
    // horizontal scrollbar rather than widening the page around it.
    expect(row!.closest("table")?.parentElement).toHaveClass("table-scroll");
  });

  it("shows a status chip per server a render has a delivery row for", async () => {
    stubFetch(
      movieRoutes({
        "/api/items/3": () =>
          json({
            ...MOVIE,
            renders: [
              {
                ...MOVIE.renders[0],
                deliveries: [
                  { server: "plex", status: "uploaded", attempted_at: null,
                    uploaded_at: "2026-08-02T18:40:00Z", next_attempt_at: null, detail: null },
                  { server: "jellyfin", status: "pending", attempted_at: null,
                    uploaded_at: null, next_attempt_at: "2026-08-02T19:00:00Z",
                    detail: "ConnectError: connection refused" },
                ],
              },
            ],
          }),
      }),
    );

    await renderItem();

    const row = document.querySelector(".render-table tbody tr");
    const chips = [...row!.querySelectorAll(".pill")];
    expect(chips.map((chip) => chip.textContent)).toEqual(["plex uploaded", "jellyfin pending"]);
    expect(chips[0]).toHaveClass("pill-uploaded");
    expect(chips[1]).toHaveClass("pill-pending");
    // detail is the hover text, not shown on screen.
    expect(chips[0].getAttribute("title")).toBeNull();
    expect(chips[1].getAttribute("title")).toBe("ConnectError: connection refused");
  });

  it("shows the per-server outcome table under the renders table", async () => {
    stubFetch(
      movieRoutes({
        "/api/items/3": () =>
          json({
            ...MOVIE,
            servers: [
              {
                server: "jellyfin",
                metadata: {
                  status: "failed",
                  detail: "status: HTTPStatusError 400",
                  attempts: 8,
                  attempted_at: "2026-09-14T00:00:00Z",
                  written_at: null,
                  next_attempt_at: null,
                },
                artwork: [
                  {
                    art_kind: "poster",
                    status: "uploaded",
                    detail: null,
                    attempts: 0,
                    attempted_at: "2026-09-14T00:00:00Z",
                    uploaded_at: "2026-09-14T00:00:00Z",
                    next_attempt_at: null,
                  },
                ],
              },
            ],
          }),
      }),
    );

    await renderItem();

    expect(await screen.findByText("Per server")).toBeInTheDocument();
    const table = document.querySelector(".server-outcomes");
    expect(table).not.toBeNull();
    expect(within(table as HTMLElement).getByText("metadata failed")).toBeInTheDocument();
    expect(within(table as HTMLElement).getByText("poster uploaded")).toBeInTheDocument();
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
      "/api/items/9/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
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

  it("surfaces the 503 rename failure as the endpoint's fixed sentence", async () => {
    // A contract MIRROR, not an independent pin: this test stubs the response
    // body itself, and the page renders whatever detail it is given, so it
    // cannot go red when the endpoint's wording changes. What it is for is
    // recording which wording the endpoint sends -- and roadmap row 248
    // changed that from the OS error (errno plus the absolute path on the
    // mount) to the fixed sentence api/routes.py now serves, byte-identical
    // to api/manual.py's and api/candidates.py's for the same mount. The
    // sentence that actually goes red on a backend change is
    // tests/test_api_clear_override.py::test_a_failed_rename_is_503_and_changes_nothing.
    const detail = "could not write to the override mount";
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

/** The show a season or episode belongs to, named in the header and the
 * breadcrumb -- the operator report this fixes: an episode page titled only
 * "Episode 26" with no show anywhere, indistinguishable from every other
 * "Episode 26" in the library.
 */
describe("ItemDetail parentage", () => {
  it("names the show in an episode's header, S/E numbers included, linked to the show's own item view", async () => {
    stubFetch({
      "/api/items/154245": () => json(EPISODE_WITH_PARENT),
      "/api/items/154245/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
      "/api/items/154245/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(154245);

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toContain("Firefly");
    expect(heading.textContent).toContain("S02E26");
    expect(heading.textContent).toContain("Episode 26");
    const showLink = within(heading).getByRole("link", { name: "Firefly" });
    expect(showLink).toHaveAttribute("href", "/items/42");
  });

  it("gains the show segment in the breadcrumb, linked to the show's own item view", async () => {
    stubFetch({
      "/api/items/154245": () => json(EPISODE_WITH_PARENT),
      "/api/items/154245/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
      "/api/items/154245/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(154245);

    const breadcrumb = document.querySelector(".item-meta") as HTMLElement;
    expect(breadcrumb.textContent).toContain("TV Shows");
    expect(breadcrumb.textContent).toContain("Firefly");
    expect(breadcrumb.textContent).toContain("episode");
    expect(breadcrumb.textContent).toContain("plex 154245");
    const showLink = within(breadcrumb).getByRole("link", { name: "Firefly" });
    expect(showLink).toHaveAttribute("href", "/items/42");
  });

  it("renders every server's ref, not just plex's", async () => {
    stubFetch({
      "/api/items/154245": () =>
        json({ ...EPISODE_WITH_PARENT, refs: { plex: "154245", jellyfin: "0a1b" } }),
      "/api/items/154245/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
      "/api/items/154245/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(154245);

    const breadcrumb = document.querySelector(".item-meta") as HTMLElement;
    expect(breadcrumb.textContent).toContain("plex 154245");
    expect(breadcrumb.textContent).toContain("jellyfin 0a1b");
  });

  it("shows a placeholder instead of a dangling separator when an item has no refs at all", async () => {
    stubFetch({
      "/api/items/154245": () => json({ ...EPISODE_WITH_PARENT, refs: {} }),
      "/api/items/154245/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
      "/api/items/154245/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(154245);

    const breadcrumb = document.querySelector(".item-meta") as HTMLElement;
    // The old rendering ended "episode · rating key —"; the replacement must
    // still end on a placeholder, not on the "·" separator with nothing after
    // it -- an item with no server rows is a real, servable state, not one
    // the page can afford to render badly.
    expect(breadcrumb.textContent?.trim().endsWith("—")).toBe(true);
    expect(breadcrumb.textContent).not.toMatch(/·\s*$/);
  });

  it("names the show in a season's header and breadcrumb, analogous to an episode", async () => {
    stubFetch({
      "/api/items/88": () => json(SEASON_WITH_PARENT),
      "/api/items/88/artwork/season_poster?w=640": () => imageBytes("season-poster-bytes"),
      "/api/items/88/artwork/season_poster/live": () => imageBytes("live-season-poster-bytes"),
    });

    await renderItem(88);

    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toContain("Firefly");
    expect(heading.textContent).toContain("Season 2");
    expect(within(heading).getByRole("link", { name: "Firefly" })).toHaveAttribute(
      "href",
      "/items/42",
    );

    const breadcrumb = document.querySelector(".item-meta") as HTMLElement;
    expect(within(breadcrumb).getByRole("link", { name: "Firefly" })).toHaveAttribute(
      "href",
      "/items/42",
    );
  });

  it("degrades to today's plain title when the parent is unresolved -- no invented show name", async () => {
    // EPISODE carries `parent: null`, matching an item whose upsert never
    // got a chance to fill parent_id (render/pipeline.py's
    // _upsert_media_item leaves it null rather than inventing one).
    stubFetch({
      "/api/items/9": () => json(EPISODE),
      "/api/items/9/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
      "/api/items/9/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(9);

    const heading = screen.getByRole("heading", { level: 1, name: "Fly" });
    expect(heading).toBeInTheDocument();
    expect(within(heading).queryByRole("link")).toBeNull();

    const breadcrumb = document.querySelector(".item-meta") as HTMLElement;
    expect(within(breadcrumb).queryAllByRole("link")).toHaveLength(1); // "Library" only
  });

  it("leaves a movie's header and breadcrumb unchanged -- a movie has no parent", async () => {
    stubFetch(movieRoutes());

    await renderItem();

    const heading = screen.getByRole("heading", { level: 1, name: "Ghostbusters" });
    expect(within(heading).queryByRole("link")).toBeNull();

    const breadcrumb = document.querySelector(".item-meta") as HTMLElement;
    expect(within(breadcrumb).queryAllByRole("link")).toHaveLength(1); // "Library" only
  });
});

/** The candidate picker.
 *
 * Two things here are not ordinary UI wiring and are asserted as such: the
 * pick body must carry the FULL-SIZE `url` (the server refuses anything it did
 * not itself offer, and a thumbnail URL is not one of those), and the panel
 * must carry the provider attribution, which is a licence condition of showing
 * TMDB's and TheTVDB's artwork at all -- and this panel is the first surface in
 * the application that shows it as theirs.
 */
describe("ItemDetail candidate picker", () => {
  it("expands an inline panel of hot-linked provider tiles", async () => {
    stubFetch(
      movieRoutes({ "/api/items/3/candidates/poster": () => json(CANDIDATES) }),
    );

    await renderItem();
    // Nothing is fetched and nothing is shown until it is asked for: browsing
    // costs upstream provider calls per item.
    expect(document.querySelector(".candidate-panel")).toBeNull();

    await openPanel("Browse candidates");

    const images = tiles().map((tile) => tile.querySelector("img")!);
    // Straight into `src`, and the provider's own URL: these are public and
    // unauthenticated. Our own image endpoints cannot be loaded this way (they
    // need a bearer header) and go through apiFetchImage instead -- the two
    // must never be confused, so the exact strings are compared.
    expect(images.map((image) => image.getAttribute("src"))).toEqual([
      TMDB_THUMB,
      TVDB_URL,
    ]);
    expect(images.map((image) => image.getAttribute("loading"))).toEqual([
      "lazy",
      "lazy",
    ]);

    const text = tiles().map((tile) => tile.textContent ?? "");
    expect(text[0]).toContain("tmdb");
    expect(text[0]).toContain("en");
    expect(text[0]).toContain("2000×3000");
    expect(text[1]).toContain("tvdb");
    expect(text[1]).toContain("fi");
    expect(text[1]).toContain("1000×1500");
    // TheTVDB is the only provider that reports textlessness; `false` there
    // means no burned-in text, and `null` on the TMDB tile means unknown --
    // which must not be shown as though it were an answer.
    expect(text[1]).toContain("textless");
    expect(text[0]).not.toContain("textless");
  });

  it("marks the candidate the render row is already using", async () => {
    stubFetch(
      movieRoutes({ "/api/items/3/candidates/poster": () => json(CANDIDATES) }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    const marked = tiles().filter((tile) => tile.classList.contains("is-current"));
    expect(marked).toHaveLength(1);
    // The one whose `url` is the response's `current.source_url` -- not the
    // first, and not the highest-scoring.
    expect(marked[0].querySelector("img")!.getAttribute("src")).toBe(TVDB_URL);
  });

  it("lists a provider's failure beside the tiles, not instead of them", async () => {
    stubFetch(
      movieRoutes({ "/api/items/3/candidates/poster": () => json(CANDIDATES) }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    // The endpoint sends the exception's class name, never a sentence. Shown as
    // one, it would read as the page's own diagnosis of the failure.
    expect(candidatePanel().querySelector(".candidate-errors")?.textContent).toContain(
      "fanart unavailable (ReadTimeout)",
    );
    // A failing provider costs its own rows only: this is a partial result, not
    // an error state.
    expect(tiles()).toHaveLength(2);
  });

  it("picks a candidate by posting exactly that tile's provider and full-size url", async () => {
    let detailCalls = 0;
    const fetchMock = stubFetch(
      movieRoutes({
        "/api/items/3": () => {
          detailCalls += 1;
          return json(MOVIE);
        },
        "/api/items/3/candidates/poster": () => json(CANDIDATES),
        "/api/items/3/candidates/poster/pick": () =>
          json({ status: "picked", queued: true }),
      }),
    );

    await renderItem();
    await openPanel("Browse candidates");
    expect(detailCalls).toBe(1);

    fireEvent.click(within(tiles()[0]).getByRole("button", { name: "Pick" }));

    await waitFor(() =>
      expect(document.querySelector(".candidate-note")).not.toBeNull(),
    );

    const post = fetchMock.mock.calls.find(
      (call) => call[0] === "/api/items/3/candidates/poster/pick",
    );
    expect(post).toBeDefined();
    expect(post![1]?.method).toBe("POST");
    // The body compared whole. The server treats the URL as a claim and refuses
    // any string it did not itself offer, so posting `thumb_url` -- the one
    // string this tile actually displays -- is a 422 in production and must be
    // a red test here.
    expect(JSON.parse(post![1]!.body as string)).toEqual({
      provider: "tmdb",
      url: TMDB_URL,
    });

    // The row's provider flips to "manual" and its fingerprints are nulled
    // server-side, so the table on screen is stale until the item is re-read.
    expect(detailCalls).toBe(2);
    expect(document.querySelector(".candidate-note")!.textContent).toContain("queued");
  });

  it("warns on the Pick control that an existing override is not kept", async () => {
    const MANUAL_CURRENT = {
      ...CANDIDATES,
      current: { source_url: OVERRIDE_PATH, provider: "manual" },
    };
    stubFetch(
      bothKindRoutes({
        "/api/items/3/candidates/poster": () => json(MANUAL_CURRENT),
        "/api/items/3/candidates/background": () => json(CANDIDATES),
      }),
    );

    await renderItem();

    // The poster row is the manual one; sections are ordered background first.
    fireEvent.click(
      within(kindSections()[1]).getByRole("button", { name: "Browse candidates" }),
    );
    await waitFor(() => expect(tiles().length).toBeGreaterThan(0));

    // A pick overwrites the operator's own override file with no backup kept.
    // That is deliberate -- the mount is theirs, not ours to version -- which
    // is exactly why the control has to say so before it is clicked.
    for (const tile of tiles()) {
      expect(tile.querySelector("button")!.getAttribute("title")).toContain(
        "the previous file is not kept",
      );
    }

    // ...and does not say it where there is no override to destroy, or the
    // warning means nothing anywhere.
    fireEvent.click(
      within(kindSections()[0]).getByRole("button", { name: "Browse candidates" }),
    );
    await waitFor(() =>
      expect(candidatePanel().closest(".art-kind-panes")).toBe(kindSections()[0]),
    );
    for (const tile of tiles()) {
      expect(tile.querySelector("button")!.getAttribute("title") ?? "").not.toContain(
        "the previous file is not kept",
      );
    }
  });

  it("carries the provider attribution inside the panel itself", async () => {
    // Not "somewhere in the application": this panel is the first surface that
    // shows TMDB's and TheTVDB's artwork as theirs, and both providers' terms
    // make the notice a condition of doing so. Settings carrying it does not
    // discharge that for a page an operator can reach without ever opening
    // Settings.
    stubFetch(
      movieRoutes({ "/api/items/3/candidates/poster": () => json(CANDIDATES) }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    const panel = candidatePanel();
    expect(panel.textContent).toContain(TMDB_NOTICE);
    expect(panel.textContent).toContain(TVDB_NOTICE);
    expect(within(panel).getByAltText("TMDB")).toBeInTheDocument();
    expect(
      within(panel).getByRole("link", { name: /TheTVDB/i }).getAttribute("href"),
    ).toBe("https://thetvdb.com");
    // TMDB's terms require their logo to be less prominent than this
    // application's own branding, so the mark it is weighed against has to be
    // in the same block (item.css does the weighing).
    expect(panel.querySelector(".brand-mark")?.textContent).toBe("Autoposter");
  });

  it("browses logos from the poster section, and only for kinds that use one", async () => {
    const fetchMock = stubFetch(
      movieRoutes({
        "/api/items/3/candidates/logo": () => json({ ...CANDIDATES, current: null }),
      }),
    );

    const view = await renderItem();
    await openPanel("Browse logos");

    // The logo endpoint, not the poster one: a logo is a separate art kind at
    // the provider layer even though it is composited into the poster.
    expect(
      fetchMock.mock.calls
        .map((call) => call[0] as string)
        .filter((path) => path.includes("/candidates/")),
    ).toEqual(["/api/items/3/candidates/logo"]);
    // There is no render row for a logo, so `current` is null and nothing is
    // marked as in use.
    expect(tiles().some((tile) => tile.classList.contains("is-current"))).toBe(false);

    view.unmount();
    stubFetch({
      "/api/items/9": () => json(EPISODE),
      "/api/items/9/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
      "/api/items/9/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });
    await renderItem(9);

    // Only a movie's or a show's poster render composites a logo; offering the
    // browse on an episode would invite a pick nothing would ever consume.
    expect(screen.queryByRole("button", { name: "Browse logos" })).toBeNull();
    expect(screen.getByRole("button", { name: "Browse candidates" })).toBeInTheDocument();
  });

  it("reports a refused pick instead of claiming the image was taken", async () => {
    let detailCalls = 0;
    stubFetch(
      movieRoutes({
        "/api/items/3": () => {
          detailCalls += 1;
          return json(MOVIE);
        },
        "/api/items/3/candidates/poster": () => json(CANDIDATES),
        "/api/items/3/candidates/poster/pick": () =>
          json({ detail: "that image is not one of this item's candidates" }, 422),
      }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    fireEvent.click(within(tiles()[0]).getByRole("button", { name: "Pick" }));

    await waitFor(() =>
      expect(candidatePanel().querySelector(".candidate-error")?.textContent).toBe(
        "that image is not one of this item's candidates",
      ),
    );
    // An error, not a note: a panel showing both would be saying the pick was
    // refused and taken at once.
    expect(document.querySelector(".candidate-note")).toBeNull();
    // Nothing changed on the server, so nothing needed re-reading.
    expect(detailCalls).toBe(1);
  });

  it("disables every Pick while one is in flight", async () => {
    // A pick overwrites the override file outright. Two in flight for one art
    // kind is a race over which image the operator ends up with.
    let release!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    stubFetch(
      movieRoutes({
        "/api/items/3/candidates/poster": () => json(CANDIDATES),
        "/api/items/3/candidates/poster/pick": () => pending,
      }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    const picks = () => screen.getAllByRole("button", { name: "Pick" });
    expect(picks().every((button) => !button.hasAttribute("disabled"))).toBe(true);

    fireEvent.click(picks()[0]);

    await waitFor(() =>
      expect(picks().every((button) => button.hasAttribute("disabled"))).toBe(true),
    );

    await act(async () => {
      release(json({ status: "picked", queued: false }));
    });

    await waitFor(() =>
      expect(document.querySelector(".candidate-note")).not.toBeNull(),
    );
    // What the server said: the reprocess enqueue de-duplicates, so a pick made
    // while a re-render is already pending queues nothing new.
    expect(document.querySelector(".candidate-note")!.textContent).not.toMatch(
      /queued a re-render/,
    );
  });

  it("shows each render row's source by host, and an override path as plain text", async () => {
    stubFetch(bothKindRoutes());

    await renderItem();

    const source = (artKind: string) =>
      [...renderRow(artKind).querySelectorAll("td")][3];

    // Under an override this field is an absolute path on the operator's own
    // mount, not a URL. Turned into an href or an <img src> -- one line from
    // what the tiles above legitimately do -- it is a broken link at best.
    expect(source("poster").textContent).toContain(OVERRIDE_PATH);
    expect(source("poster").querySelector("a")).toBeNull();
    expect(source("poster").querySelector("img")).toBeNull();
    // No textlessness was recorded for an override: there was no candidate to
    // ask, and claiming one would be inventing provenance.
    expect(source("poster").textContent).not.toContain("textless");

    // A provider URL is shown by host: the table is nowrap, and a full TMDB URL
    // pushes every other column off the screen.
    expect(source("background").textContent).toContain("image.tmdb.org");
    expect(source("background").textContent).not.toContain("/t/p/original");
    expect(source("background").textContent).toContain("textless");
  });

  it("clears a prior pick's note when the browsed art kind changes", async () => {
    // A successful pick leaves a note in this instance's own state. Switching
    // from posters to logos must not carry it over -- the tiles underneath
    // are a different art kind's candidates entirely, and the note would read
    // as though it were about them.
    stubFetch(
      movieRoutes({
        "/api/items/3/candidates/poster": () => json(CANDIDATES),
        "/api/items/3/candidates/logo": () => json({ ...CANDIDATES, current: null }),
        "/api/items/3/candidates/poster/pick": () =>
          json({ status: "picked", queued: true }),
      }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    fireEvent.click(within(tiles()[0]).getByRole("button", { name: "Pick" }));
    await waitFor(() =>
      expect(document.querySelector(".candidate-note")).not.toBeNull(),
    );

    fireEvent.click(screen.getByRole("button", { name: "Browse logos" }));
    await waitFor(() => expect(tiles().length).toBeGreaterThan(0));

    expect(document.querySelector(".candidate-note")).toBeNull();
    expect(document.querySelector(".candidate-error")).toBeNull();
  });

  it("marks the newly-picked tile as pending, against the response production actually returns", async () => {
    // The re-read at the end of `pick` was already happening; what it comes
    // back with is the point. `provider="manual"` is stamped by the RENDER
    // (render/pipeline.py:1250), and a pick touches only the two fingerprint
    // columns (api/candidates.py) -- so the browse endpoint, which derives
    // `current` from the Render row, answers the SAME pre-pick provenance it
    // answered a moment ago. This stub returns exactly that, which is why the
    // mark cannot come from `current` at all. The version of this test that
    // shipped in 6d stubbed a second response moving `current` to the picked
    // tile: green, and describing a server that does not exist.
    let candidateCalls = 0;
    stubFetch(
      movieRoutes({
        "/api/items/3/candidates/poster": () => {
          candidateCalls += 1;
          return json(CANDIDATES);
        },
        "/api/items/3/candidates/poster/pick": () =>
          json({ status: "picked", queued: true }),
      }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    // Before the pick: the tvdb tile (index 1) is the one in use.
    expect(tiles()[1].classList.contains("is-current")).toBe(true);
    expect(tiles()[0].classList.contains("is-current")).toBe(false);

    fireEvent.click(within(tiles()[0]).getByRole("button", { name: "Pick" }));
    await waitFor(() =>
      expect(document.querySelector(".candidate-note")).not.toBeNull(),
    );

    // The re-read still happens: the errors list and the Renders table are read
    // from it, and a server that one day DID move `current` would be honoured.
    expect(candidateCalls).toBe(2);
    // After the pick, with `current` unchanged: the picked tile is marked
    // pending, and the tile the server still calls current has stopped saying
    // so -- it names the file that has just been replaced.
    expect(tiles()[0].classList.contains("is-picked")).toBe(true);
    expect(tiles()[0].textContent).toContain("picked · re-render pending");
    expect(tiles()[1].classList.contains("is-current")).toBe(false);
    expect(candidatePanel().textContent).not.toContain("in use");
  });

  it("keeps the pending mark when the panel is closed and reopened", async () => {
    stubFetch(
      movieRoutes({
        "/api/items/3/candidates/poster": () => json(CANDIDATES),
        "/api/items/3/candidates/poster/pick": () =>
          json({ status: "picked", queued: true }),
      }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    fireEvent.click(within(tiles()[0]).getByRole("button", { name: "Pick" }));
    await waitFor(() =>
      expect(document.querySelector(".candidate-note")).not.toBeNull(),
    );
    expect(tiles()[0].querySelector(".candidate-picked")?.textContent).toBe(
      "picked · re-render pending",
    );

    // The close/reopen an operator actually performs. A flag held inside
    // CandidatePanel dies exactly here -- the panel is keyed on the art kind
    // and unmounted by the toggle -- which is why the set lives in ItemDetail.
    fireEvent.click(screen.getByRole("button", { name: "Browse candidates" }));
    expect(document.querySelector(".candidate-panel")).toBeNull();
    await openPanel("Browse candidates");

    expect(tiles()[0].querySelector(".candidate-picked")?.textContent).toBe(
      "picked · re-render pending",
    );
    // The reopened panel re-fetched, and its `current` is still tvdb's -- which
    // is exactly the stale claim that must not come back with it.
    expect(tiles().some((tile) => tile.classList.contains("is-current"))).toBe(false);
  });

  it("warns that an existing override is not kept as soon as a pick has been made", async () => {
    // The fixture's render row is tvdb's, so nothing warns before the pick.
    // After it, an override file exists on the mount that the server cannot
    // report yet -- and a SECOND pick in that window would overwrite it with no
    // backup. That silent window is the half of the defect an operator loses a
    // file to.
    stubFetch(
      movieRoutes({
        "/api/items/3/candidates/poster": () => json(CANDIDATES),
        "/api/items/3/candidates/poster/pick": () =>
          json({ status: "picked", queued: true }),
      }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    for (const tile of tiles()) {
      expect(tile.querySelector("button")!.getAttribute("title") ?? "").not.toContain(
        "the previous file is not kept",
      );
    }

    fireEvent.click(within(tiles()[0]).getByRole("button", { name: "Pick" }));
    await waitFor(() =>
      expect(document.querySelector(".candidate-note")).not.toBeNull(),
    );

    // Every tile, not only the one just picked: the warning is about the file
    // on the mount, and any of these buttons would replace it.
    for (const tile of tiles()) {
      expect(tile.querySelector("button")!.getAttribute("title")).toContain(
        "the previous file is not kept",
      );
    }
  });

  it("marks an SVG candidate unpickable instead of hiding it, and its Pick fires nothing", async () => {
    const fetchMock = stubFetch(
      movieRoutes({
        "/api/items/3/candidates/logo": () => json(LOGO_CANDIDATES),
      }),
    );

    await renderItem();
    await openPanel("Browse logos");

    // Shown, not filtered. The compositor rasterises an SVG clearlogo
    // (build_logo_argv's -density 300 branch) and the render path leaves
    // `raster_only` off for exactly that reason, so the automatic ladder may
    // already be using the very image a filter would have hidden. Only the pick
    // cannot take it.
    expect(tiles()).toHaveLength(2);
    expect(tiles()[0].querySelector("img")!.getAttribute("src")).toBe(TMDB_SVG_THUMB);
    expect(tiles()[0].classList.contains("is-unpickable")).toBe(true);

    const button = within(tiles()[0]).getByRole("button", { name: "Pick" });
    expect(button.hasAttribute("disabled")).toBe(true);
    // Ours and fixed: never the provider's URL, a Content-Type header, or an
    // exception message. It names the reason -- the pick decodes the image and
    // Pillow has no SVG decoder -- rather than leaving the operator to guess.
    expect(tiles()[0].textContent).toContain("cannot be picked");
    expect(tiles()[0].textContent).toContain("Pillow has no SVG decoder");
    expect(button.getAttribute("title")).toContain("cannot be picked");

    fireEvent.click(button);
    await act(async () => {});

    // No request at all. Sent, it would come back 502 "could not fetch the
    // picked image from tmdb" -- which reads as the provider failing rather
    // than as this image kind being unsupported.
    expect(
      fetchMock.mock.calls
        .map((call) => call[0] as string)
        .filter((path) => path.endsWith("/pick")),
    ).toEqual([]);
    expect(document.querySelector(".candidate-note")).toBeNull();
    expect(document.querySelector(".candidate-error")).toBeNull();
  });

  it("leaves the raster candidate beside it pickable", async () => {
    // The refusal is per tile, not per grid: an SVG in the list must not cost
    // the operator the logo they can actually install.
    stubFetch(
      movieRoutes({
        "/api/items/3/candidates/logo": () => json(LOGO_CANDIDATES),
        "/api/items/3/candidates/logo/pick": () =>
          json({ status: "picked", queued: true }),
      }),
    );

    await renderItem();
    await openPanel("Browse logos");

    const button = within(tiles()[1]).getByRole("button", { name: "Pick" });
    expect(button.hasAttribute("disabled")).toBe(false);
    expect(tiles()[1].classList.contains("is-unpickable")).toBe(false);
    expect(tiles()[1].textContent).not.toContain("cannot be picked");

    fireEvent.click(button);
    await waitFor(() =>
      expect(document.querySelector(".candidate-note")).not.toBeNull(),
    );

    expect(tiles()[1].querySelector(".candidate-picked")?.textContent).toBe(
      "picked · re-render pending",
    );
    // ...and the SVG tile beside it is still refused after a pick has landed.
    expect(
      within(tiles()[0]).getByRole("button", { name: "Pick" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("marks the pick pending and warns immediately even when the post-pick re-read fails", async () => {
    // The POST above has already written the override file and nulled the
    // render row's two fingerprints before this second call ever runs -- so a
    // failure here must not undo the pending mark or the warning. The re-read
    // is not a cheap call (it re-runs the whole provider fan-out), so a
    // timeout here is the ordinary failure, not an exotic one. Moving
    // `onPickedUrl` back to after the `Promise.all` reddens the first two
    // assertions below.
    let candidateCalls = 0;
    stubFetch(
      movieRoutes({
        "/api/items/3/candidates/poster": () => {
          candidateCalls += 1;
          if (candidateCalls === 1) return json(CANDIDATES);
          throw new Error("network error");
        },
        "/api/items/3/candidates/poster/pick": () =>
          json({ status: "picked", queued: true }),
      }),
    );

    await renderItem();
    await openPanel("Browse candidates");

    fireEvent.click(within(tiles()[0]).getByRole("button", { name: "Pick" }));

    await waitFor(() =>
      expect(candidatePanel().querySelector(".candidate-error")).not.toBeNull(),
    );

    // The mark and the warning describe the file on the mount, not the
    // freshness of the re-read -- both are on although the re-read rejected.
    expect(tiles()[0].classList.contains("is-picked")).toBe(true);
    expect(tiles()[0].textContent).toContain("picked · re-render pending");
    for (const tile of tiles()) {
      expect(tile.querySelector("button")!.getAttribute("title")).toContain(
        "the previous file is not kept",
      );
    }
    // The error is still surfaced -- the fix does not paper over the failure,
    // it only stops the failure from erasing the pick's own honesty.
    expect(candidatePanel().querySelector(".candidate-error")!.textContent).toBe(
      "network error",
    );
    expect(document.querySelector(".candidate-note")).toBeNull();
  });

  it("clears the picked-this-visit map when the item changes", async () => {
    // The map is keyed by art kind, not by item -- so the leak this case pins
    // only shows up when item B is browsed under the SAME art kind item A was
    // picked under. Both items use "title_card" here for exactly that reason;
    // a second item that happened to use "poster" instead would pass whether
    // or not the reset exists, which is no test at all. The episode's own art
    // kind is picked, then the parent-show link -- the navigation an operator
    // actually performs, staying inside the same ItemDetail instance rather
    // than a fresh mount -- carries the page to item B. Deleting
    // `setPickedUrls({})` from the `[itemId]` effect must turn exactly this
    // case red.
    const OTHER_ITEM = {
      ...MOVIE,
      id: 42,
      title: "Firefly",
      refs: { plex: "42042" },
      renders: [{ ...MOVIE.renders[0], art_kind: "title_card" }],
    };
    stubFetch({
      "/api/items/154245": () => json(EPISODE_WITH_PARENT),
      "/api/items/154245/artwork/title_card?w=640": () => imageBytes("title-card-bytes"),
      "/api/items/154245/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
      "/api/items/154245/candidates/title_card": () => json(CANDIDATES),
      "/api/items/154245/candidates/title_card/pick": () =>
        json({ status: "picked", queued: true }),
      "/api/items/42": () => json(OTHER_ITEM),
      "/api/items/42/artwork/title_card?w=640": () => imageBytes("base-image-bytes"),
      "/api/items/42/artwork/title_card/live": () => imageBytes("live-image-bytes"),
      "/api/items/42/candidates/title_card": () => json(CANDIDATES),
    });

    await renderItem(154245);
    await openPanel("Browse candidates");

    fireEvent.click(within(tiles()[0]).getByRole("button", { name: "Pick" }));
    await waitFor(() =>
      expect(document.querySelector(".candidate-note")).not.toBeNull(),
    );
    expect(tiles()[0].classList.contains("is-picked")).toBe(true);

    const showLink = within(screen.getByRole("heading", { level: 1 })).getByRole(
      "link",
      { name: "Firefly" },
    );
    fireEvent.click(showLink);

    await screen.findByRole("heading", { level: 1, name: "Firefly" });
    await act(async () => {});

    await openPanel("Browse candidates");
    // Item B's tiles carry no pending mark for a re-render nobody queued on
    // item B.
    expect(tiles().some((tile) => tile.classList.contains("is-picked"))).toBe(false);
    expect(candidatePanel().textContent).not.toContain("picked · re-render pending");
    for (const tile of tiles()) {
      expect(tile.querySelector("button")!.getAttribute("title") ?? "").not.toContain(
        "the previous file is not kept",
      );
    }
  });

  it("closes the panel when Browse candidates is clicked a second time", async () => {
    stubFetch(movieRoutes({ "/api/items/3/candidates/poster": () => json(CANDIDATES) }));

    await renderItem();
    await openPanel("Browse candidates");
    expect(document.querySelector(".candidate-panel")).not.toBeNull();

    // The same button is the way back out.
    fireEvent.click(screen.getByRole("button", { name: "Browse candidates" }));

    expect(document.querySelector(".candidate-panel")).toBeNull();
  });

  it("closes one section's panel when another section's browse is opened", async () => {
    stubFetch(
      bothKindRoutes({
        "/api/items/3/candidates/poster": () => json(CANDIDATES),
        "/api/items/3/candidates/background": () => json(CANDIDATES),
      }),
    );

    await renderItem();

    // Sections are ordered background first, poster second.
    const sections = kindSections();
    fireEvent.click(
      within(sections[0]).getByRole("button", { name: "Browse candidates" }),
    );
    await waitFor(() => expect(tiles().length).toBeGreaterThan(0));
    expect(candidatePanel().closest(".art-kind-panes")).toBe(sections[0]);

    fireEvent.click(
      within(sections[1]).getByRole("button", { name: "Browse candidates" }),
    );
    await waitFor(() =>
      expect(candidatePanel().closest(".art-kind-panes")).toBe(sections[1]),
    );

    // Only one panel exists at a time -- the first section's did not stay
    // open alongside the second's.
    expect(document.querySelectorAll(".candidate-panel")).toHaveLength(1);
  });

  // --- manual source: install a URL or a mount path -------------------------

  const MANUAL_SOURCE = "https://example.com/art/poster.jpg";

  function manualPanel(): HTMLElement {
    const panel = document.querySelector<HTMLElement>(".manual-panel");
    if (panel === null) throw new Error("no manual-source panel is open");
    return panel;
  }

  /** Opens the manual panel a button names and types a source into it. */
  function openManual(name: string, source = MANUAL_SOURCE) {
    fireEvent.click(screen.getByRole("button", { name }));
    fireEvent.change(within(manualPanel()).getByRole("textbox"), {
      target: { value: source },
    });
  }

  it("opens a manual-source panel that states the two source forms", async () => {
    stubFetch(movieRoutes());

    await renderItem();
    expect(document.querySelector(".manual-panel")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Use file or URL" }));

    // Both forms the endpoint accepts, named so the operator does not have to
    // guess: an https URL, or a path on the mount.
    expect(manualPanel().textContent).toContain("https://");
    expect(manualPanel().textContent).toContain("/manualassets");
  });

  it("installs a manual source by posting exactly the source field, then re-reads", async () => {
    let detailCalls = 0;
    const fetchMock = stubFetch(
      movieRoutes({
        "/api/items/3": () => {
          detailCalls += 1;
          return json(MOVIE);
        },
        "/api/items/3/renders/poster/manual": () =>
          json({ status: "installed", queued: true }),
      }),
    );

    await renderItem();
    expect(detailCalls).toBe(1);
    openManual("Use file or URL");

    fireEvent.click(within(manualPanel()).getByRole("button", { name: "Install" }));

    await waitFor(() =>
      expect(manualPanel().querySelector(".candidate-note")).not.toBeNull(),
    );

    const post = fetchMock.mock.calls.find(
      (call) => call[0] === "/api/items/3/renders/poster/manual",
    );
    expect(post).toBeDefined();
    expect(post![1]?.method).toBe("POST");
    // The body compared whole. The endpoint reads exactly `source`; a control
    // that named the field anything else is a 422 in production and must be a
    // red test here.
    expect(JSON.parse(post![1]!.body as string)).toEqual({ source: MANUAL_SOURCE });

    // The row's provider flips to "manual" and its fingerprints are nulled
    // server-side, so the item is re-read before the outcome is reported.
    expect(detailCalls).toBe(2);
    // Pinned exactly, not just toContain("queued"): the URL and upload
    // branches share one `installedNote` helper, and this is half of the
    // proof that they cannot drift apart.
    expect(manualPanel().querySelector(".candidate-note")!.textContent).toBe(
      "Installed. The image was written to the mount and a re-render was queued.",
    );
  });

  it("shows a refused manual source inline, without claiming it was installed", async () => {
    let detailCalls = 0;
    stubFetch(
      movieRoutes({
        "/api/items/3": () => {
          detailCalls += 1;
          return json(MOVIE);
        },
        "/api/items/3/renders/poster/manual": () =>
          // The guard's refusal reason -- deliberately carries no URL.
          json({ detail: "that source was refused: address is not a public host" }, 422),
      }),
    );

    await renderItem();
    openManual("Use file or URL");

    fireEvent.click(within(manualPanel()).getByRole("button", { name: "Install" }));

    await waitFor(() =>
      expect(manualPanel().querySelector(".candidate-error")?.textContent).toBe(
        "that source was refused: address is not a public host",
      ),
    );
    // An error, not a note: showing both would say the install was refused and
    // taken at once.
    expect(manualPanel().querySelector(".candidate-note")).toBeNull();
    // Nothing changed on the server, so nothing needed re-reading.
    expect(detailCalls).toBe(1);
  });

  it("installs a logo from the poster section's manual control", async () => {
    const fetchMock = stubFetch(
      movieRoutes({
        "/api/items/3/renders/logo/manual": () =>
          json({ status: "installed", queued: false }),
      }),
    );

    await renderItem();
    openManual("Use logo file or URL", "/manualassets/logo.png");

    fireEvent.click(within(manualPanel()).getByRole("button", { name: "Install" }));

    await waitFor(() =>
      expect(manualPanel().querySelector(".candidate-note")).not.toBeNull(),
    );

    // The logo art_kind, not the poster one: a logo is a separate art kind at
    // this endpoint even though it rides into the poster row server-side.
    const post = fetchMock.mock.calls.find(
      (call) => call[0] === "/api/items/3/renders/logo/manual",
    );
    expect(post).toBeDefined();
    expect(JSON.parse(post![1]!.body as string)).toEqual({ source: "/manualassets/logo.png" });
  });

  // --- manual source: a file picked from this machine ------------------------

  /** The picker inside whichever manual panel is open. */
  function filePicker(): HTMLInputElement {
    const input = manualPanel().querySelector<HTMLInputElement>("input[type=file]");
    if (input === null) throw new Error("the manual panel has no file picker");
    return input;
  }

  function chooseFile(name = "chosen.png") {
    const file = new File(["png-bytes"], name, { type: "image/png" });
    fireEvent.change(filePicker(), { target: { files: [file] } });
    return file;
  }

  it("offers a file picker beside the URL input", async () => {
    stubFetch(movieRoutes());

    await renderItem();
    fireEvent.click(screen.getByRole("button", { name: "Use file or URL" }));

    // Both sources in one panel: the text input the endpoint's URL and mount
    // paths go into, and the picker for a file on this machine.
    expect(within(manualPanel()).getByRole("textbox")).toBeInTheDocument();
    expect(filePicker().accept).toBe("image/png,image/jpeg,image/webp");
    // Nothing to send yet, so nothing to click.
    expect(within(manualPanel()).getByRole("button", { name: "Upload" })).toBeDisabled();
  });

  it("uploads the chosen file as multipart, then re-reads the item", async () => {
    let detailCalls = 0;
    const fetchMock = stubFetch(
      movieRoutes({
        "/api/items/3": () => {
          detailCalls += 1;
          return json(MOVIE);
        },
        "/api/items/3/renders/poster/manual/upload": () =>
          json({ status: "installed", queued: true }),
      }),
    );

    await renderItem();
    expect(detailCalls).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Use file or URL" }));
    const file = chooseFile();

    fireEvent.click(within(manualPanel()).getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(manualPanel().querySelector(".candidate-note")).not.toBeNull(),
    );

    const post = fetchMock.mock.calls.find(
      (call) => call[0] === "/api/items/3/renders/poster/manual/upload",
    );
    expect(post).toBeDefined();
    expect(post![1]?.method).toBe("POST");
    // The part name the endpoint reads is `file`; any other name is a 422
    // there and must be a red test here.
    const body = post![1]!.body as FormData;
    expect(body).toBeInstanceOf(FormData);
    // Not `toBe(file)`: the fixed third argument to `append` wraps the blob in
    // a new File named "upload", so the browser's own file name never leaves
    // the client. Content and type still travel unchanged.
    const uploaded = body.get("file") as File;
    expect(uploaded.name).toBe("upload");
    expect(uploaded.size).toBe(file.size);
    expect(uploaded.type).toBe(file.type);
    // No header of our own: the browser writes multipart/form-data with the
    // boundary it chose.
    expect((post![1]!.headers as Headers).has("Content-Type")).toBe(false);

    // The row's fingerprints were nulled server-side, so the item is re-read
    // before the outcome is reported -- the same contract the URL install has.
    expect(detailCalls).toBe(2);
    // Pinned exactly, matching the URL install's own assertion above -- both
    // branches share one `installedNote` helper.
    expect(manualPanel().querySelector(".candidate-note")!.textContent).toBe(
      "Installed. The image was written to the mount and a re-render was queued.",
    );
    // Reset after success: a second click cannot silently re-post the same
    // file, and re-picking it would not even fire a change event to notice.
    expect(filePicker().value).toBe("");
    expect(within(manualPanel()).getByRole("button", { name: "Upload" })).toBeDisabled();
  });

  it("shows a refused upload inline, without claiming it was installed", async () => {
    stubFetch(
      movieRoutes({
        "/api/items/3/renders/poster/manual/upload": () =>
          json({ detail: "the upload exceeds the size cap" }, 413),
      }),
    );

    await renderItem();
    fireEvent.click(screen.getByRole("button", { name: "Use file or URL" }));
    chooseFile();

    fireEvent.click(within(manualPanel()).getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(manualPanel().querySelector(".candidate-error")?.textContent).toBe(
        "the upload exceeds the size cap",
      ),
    );
    // An error, not a note: showing both would say the upload was refused and
    // taken at once.
    expect(manualPanel().querySelector(".candidate-note")).toBeNull();
  });

  it("refuses an oversized file before sending it, with the server's own sentence", async () => {
    // The upload endpoint is deliberately left unregistered: if the picker
    // ever fell through to fetch for an oversized file, stubFetch's own
    // "unexpected path" throw would fail this test loudly rather than the
    // check silently passing because the network happened to be stubbed.
    const fetchMock = stubFetch(movieRoutes());

    await renderItem();
    fireEvent.click(screen.getByRole("button", { name: "Use file or URL" }));
    const big = new File(["png-bytes"], "big.png", { type: "image/png" });
    Object.defineProperty(big, "size", { value: 50 * 1024 * 1024 + 1 });
    fireEvent.change(filePicker(), { target: { files: [big] } });

    fireEvent.click(within(manualPanel()).getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(manualPanel().querySelector(".candidate-error")?.textContent).toBe(
        "the upload exceeds the size cap",
      ),
    );
    expect(manualPanel().querySelector(".candidate-note")).toBeNull();
    // Never sent: a 413 with an undrained body can be lost to a connection
    // reset, so the cap is enforced here before anything leaves the browser.
    const post = fetchMock.mock.calls.find(
      (call) => call[0] === "/api/items/3/renders/poster/manual/upload",
    );
    expect(post).toBeUndefined();
  });

  it("offers no picker for a logo, which the upload endpoint refuses", async () => {
    stubFetch(movieRoutes());

    await renderItem();
    fireEvent.click(screen.getByRole("button", { name: "Use logo file or URL" }));

    // A logo's stored name is derived from the source's own name, which an
    // upload has none of, so the endpoint refuses it -- offering the control
    // would be offering a 422.
    expect(manualPanel().querySelector("input[type=file]")).toBeNull();
    expect(within(manualPanel()).getByRole("textbox")).toBeInTheDocument();
    // The help copy must not advertise a control this panel never renders --
    // an operator who reads "choose a file from this computer" here and
    // finds no picker is the exact dead end the fixed 413 sentence exists to
    // avoid on the wire.
    expect(manualPanel().querySelector(".manual-help")!.textContent).not.toContain(
      "choose a file from this computer",
    );
  });
});
