/** Provider attribution is a licence condition, not a design detail.
 *
 * TMDB's terms require their exact notice wording and their logo; TheTVDB's
 * require attribution carrying a direct link to their site. Both are
 * acceptance criteria for this phase, so they are asserted verbatim -- a test
 * that matched loosely would let a paraphrase through, and a paraphrase is
 * the thing the terms forbid.
 */
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Settings, TMDB_NOTICE } from "./Settings";

const REDACTED = "***REDACTED***";

/** Deliberately includes shapes the renderer must handle generically: nested
 * objects two levels deep, a scalar list, booleans in both states, an empty
 * string, and (in the unknown-key test below) a section the component has
 * never seen. */
const CONFIG = {
  workers: 5,
  plex: { url: "http://plex:32400", excluded_libraries: ["Muskarit", "Photos"] },
  badges: { enabled: true, upload_to_plex: false },
  notifications: { enabled: false, url: "" },
  artwork: { use_logo: true, poster: { text: { font: "Comfortaa-Medium.ttf" } } },
  secrets: { plex_token: REDACTED, tmdb_api_key: REDACTED },
};

function stubConfig(body: unknown = CONFIG) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
}

beforeEach(() => {
  setToken(null);
});

/** The config fetch resolves on the next microtask, so rendering without
 * awaiting leaves a state update outside act(). */
async function renderSettings() {
  await act(async () => {
    render(<Settings />);
  });
}

describe("Settings attribution", () => {
  it("renders TMDB's notice with their required wording, exactly", async () => {
    stubConfig();
    await renderSettings();

    expect(
      screen.getByText(
        "This product uses TMDB and the TMDB APIs but is not endorsed, " +
          "certified, or otherwise approved by TMDB.",
      ),
    ).toBeInTheDocument();
    // The constant the page renders and the string the terms specify are the
    // same string; a reworded constant would pass the check above only by
    // also failing this one.
    expect(TMDB_NOTICE).toBe(
      "This product uses TMDB and the TMDB APIs but is not endorsed, " +
        "certified, or otherwise approved by TMDB.",
    );
  });

  it("renders the TMDB logo from a hashed asset, not from the dist root", async () => {
    stubConfig();
    await renderSettings();

    const logo = screen.getByAltText("TMDB") as HTMLImageElement;
    // `/tmdb-logo.png` at the root of dist/ is not served by
    // src/autoposter/api/spa.py -- the catch-all answers it with index.html
    // as a 200, so the image silently renders broken. Bundled assets live
    // under /assets, which is mounted.
    expect(logo.getAttribute("src")).not.toBe("/tmdb-logo.png");
    expect(logo.getAttribute("src")).toBeTruthy();
  });

  it("links TheTVDB attribution directly to thetvdb.com", async () => {
    stubConfig();
    await renderSettings();

    const link = screen.getByRole("link", { name: /TheTVDB/i }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://thetvdb.com");
  });
});

describe("Settings configuration", () => {
  it("renders titled sections with rows, not a JSON dump", async () => {
    stubConfig();
    await renderSettings();

    // Top-level objects become titled panels...
    expect(screen.getByRole("heading", { name: "Plex" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Badges" })).toBeInTheDocument();
    // ...nested objects become subsections, recursively (artwork -> poster ->
    // text), with snake_case keys turned into words.
    expect(screen.getByRole("heading", { name: "Poster" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Text" })).toBeInTheDocument();
    expect(screen.getByText("Excluded libraries")).toBeInTheDocument();
    // A short scalar list renders comma-joined in its value cell.
    expect(screen.getByText("Muskarit, Photos")).toBeInTheDocument();
    // Plain scalars render as values.
    expect(screen.getByText("http://plex:32400")).toBeInTheDocument();
    expect(screen.getByText("Comfortaa-Medium.ttf")).toBeInTheDocument();
    // And the raw JSON <pre> dump is gone.
    expect(document.querySelector("pre")).toBeNull();
    expect(document.body.textContent).not.toContain("{");
  });

  it("renders redactions as a badge, never the marker string", async () => {
    stubConfig();
    await renderSettings();

    // Both secrets render the badge...
    expect(screen.getAllByText("redacted")).toHaveLength(2);
    // ...and the literal marker never reaches the page as a value.
    expect(document.body.textContent).not.toContain(REDACTED);
  });

  it("renders booleans as on/off and an empty string as (not set)", async () => {
    stubConfig();
    await renderSettings();

    // badges.enabled=true, artwork.use_logo=true -> "on";
    // badges.upload_to_plex=false, notifications.enabled=false -> "off".
    expect(screen.getAllByText("on")).toHaveLength(2);
    expect(screen.getAllByText("off")).toHaveLength(2);
    expect(screen.getByText("(not set)")).toBeInTheDocument();
  });

  it("renders a section it has never seen, without a frontend change", async () => {
    // The renderer must be driven by the response's shape, not a hard-coded
    // field list -- the config schema grows every phase. This key exists
    // nowhere in the component.
    stubConfig({
      frobnicator: { warp_factor: 9, reticulate_splines: true },
    });
    await renderSettings();

    expect(
      screen.getByRole("heading", { name: "Frobnicator" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Warp factor")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  it("surfaces a failed config read instead of loading forever", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "config unreadable" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<Settings />);

    expect(await screen.findByText("config unreadable")).toBeInTheDocument();
    // The attribution is static and must survive an API failure -- it is a
    // licence condition, not a view of server data.
    expect(screen.getByAltText("TMDB")).toBeInTheDocument();
  });
});
