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
  rating_key: "101",
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
};

/** The absolute path a manual override stamps into `source_url`. It is a path
 * on the operator's mount, not a URL: a page that turned it into an href or an
 * <img src> would emit `file:`-ish nonsense the browser cannot fetch, and on a
 * page that hot-links provider thumbnails the difference is one line of code
 * apart. Asserted below to be plain text. */
const OVERRIDE_PATH = "/manualassets/Movies/Ghostbusters (1984)/poster.jpg";

/** The server's exact words, held verbatim. The page must render what the
 * server sent and must not own a copy of this sentence -- a client-side
 * rewrite would drift from the constant `api/routes.py` actually serves, and
 * an operator would be reading two different explanations of one condition. */
const TWIN_NOTE =
  "another row carries this item's identity under a different Plex rating " +
  "key, so this re-run may complete without changing anything; the " +
  "plex_merge job is what reconciles such a pair";

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
  rating_key: "909",
  season_number: null,
  episode_number: null,
  parent: null,
  facts: null,
  renders: [],
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
  rating_key: "154245",
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
  rating_key: "8080",
  season_number: 2,
  episode_number: null,
  parent: { id: 42, title: "Firefly" },
  facts: null,
  renders: [],
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
      // Same for a null source_url: an em dash, not "null" and not an empty
      // cell that reads as a rendering failure.
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

    // Nine nowrapped columns cannot fit a phone, so the table carries its own
    // horizontal scrollbar rather than widening the page around it.
    expect(row!.closest("table")?.parentElement).toHaveClass("table-scroll");
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

  it("shows the server's twin note beside the queued outcome", async () => {
    // The fork stop's whole visible symptom: the job is queued, completes,
    // and does nothing. The note is the only thing on any served surface that
    // says why -- job.last_error is deliberately null and /api/events never
    // serves the payload that would tie its audit row to this item.
    stubFetch(
      movieRoutes({
        "/api/items/3/reprocess": () =>
          json({ queued: true, job_id: 412, note: TWIN_NOTE }),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));

    await waitFor(() =>
      expect(document.querySelector(".item-twin-note")?.textContent).toBe(TWIN_NOTE),
    );
    // Its own line, not folded into the outcome: the outcome is about this
    // click, the note is about the row, and blurring them would have the page
    // claim the queueing itself was doubtful.
    expect(document.querySelector(".item-outcome")?.textContent).toBe(
      "Queued as job #412.",
    );
  });

  it("shows the twin note on a de-duplicated re-run too", async () => {
    // The note is a property of the ROW, not of whether this particular click
    // inserted a job. A page that gated it on `queued` would go silent exactly
    // when an operator is clicking twice because nothing appeared to happen.
    stubFetch(
      movieRoutes({
        "/api/items/3/reprocess": () =>
          json({ queued: false, job_id: null, note: TWIN_NOTE }),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));

    await waitFor(() =>
      expect(document.querySelector(".item-outcome")?.textContent).toBe(
        "Already queued — nothing new was added.",
      ),
    );
    expect(document.querySelector(".item-twin-note")?.textContent).toBe(TWIN_NOTE);
  });

  it("shows no twin note when the server sends none", async () => {
    // The shipped case, and the one that must stay quiet: a note on every
    // re-run would be wallpaper an operator learns to ignore.
    stubFetch(
      movieRoutes({
        "/api/items/3/reprocess": () => json({ queued: true, job_id: 412, note: null }),
      }),
    );

    await renderItem();

    fireEvent.click(screen.getByRole("button", { name: "Re-run" }));

    await waitFor(() =>
      expect(document.querySelector(".item-outcome")?.textContent).toBe(
        "Queued as job #412.",
      ),
    );
    expect(document.querySelector(".item-twin-note")).toBeNull();
  });

  it("shows no twin note when the server omits the key entirely", async () => {
    // An older API pod mid-rollout answers without a `note` key at all, so
    // response.note is undefined rather than null. A strict `!== null` check
    // would let that through and render an empty, textless paragraph.
    stubFetch(
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
    expect(document.querySelector(".item-twin-note")).toBeNull();
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

/** The show a season or episode belongs to, named in the header and the
 * breadcrumb -- the operator report this fixes: an episode page titled only
 * "Episode 26" with no show anywhere, indistinguishable from every other
 * "Episode 26" in the library.
 */
describe("ItemDetail parentage", () => {
  it("names the show in an episode's header, S/E numbers included, linked to the show's own item view", async () => {
    stubFetch({
      "/api/items/154245": () => json(EPISODE_WITH_PARENT),
      "/api/items/154245/artwork/title_card": () => imageBytes("title-card-bytes"),
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
      "/api/items/154245/artwork/title_card": () => imageBytes("title-card-bytes"),
      "/api/items/154245/artwork/title_card/live": () => imageBytes("live-title-card-bytes"),
    });

    await renderItem(154245);

    const breadcrumb = document.querySelector(".item-meta") as HTMLElement;
    expect(breadcrumb.textContent).toContain("TV Shows");
    expect(breadcrumb.textContent).toContain("Firefly");
    expect(breadcrumb.textContent).toContain("episode");
    expect(breadcrumb.textContent).toContain("rating key 154245");
    const showLink = within(breadcrumb).getByRole("link", { name: "Firefly" });
    expect(showLink).toHaveAttribute("href", "/items/42");
  });

  it("names the show in a season's header and breadcrumb, analogous to an episode", async () => {
    stubFetch({
      "/api/items/88": () => json(SEASON_WITH_PARENT),
      "/api/items/88/artwork/season_poster": () => imageBytes("season-poster-bytes"),
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
      "/api/items/9/artwork/title_card": () => imageBytes("title-card-bytes"),
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
      "/api/items/9/artwork/title_card": () => imageBytes("title-card-bytes"),
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

  it("marks the newly-picked tile as current without reopening the panel", async () => {
    // onPicked only re-reads the item; the panel's own `current` came from the
    // candidates response fetched when it opened, and a pick does not
    // otherwise touch it. Left alone, the is-current highlight and the
    // replaces-override warning would keep describing the pre-pick state
    // until the panel is closed and reopened.
    let candidateCalls = 0;
    stubFetch(
      movieRoutes({
        "/api/items/3/candidates/poster": () => {
          candidateCalls += 1;
          return candidateCalls === 1
            ? json(CANDIDATES)
            : json({ ...CANDIDATES, current: { source_url: TMDB_URL, provider: "tmdb" } });
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

    // After the pick: the tmdb tile (index 0) is current -- read from a
    // second candidates fetch, not the panel's stale first one.
    expect(candidateCalls).toBe(2);
    expect(tiles()[0].classList.contains("is-current")).toBe(true);
    expect(tiles()[1].classList.contains("is-current")).toBe(false);
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
