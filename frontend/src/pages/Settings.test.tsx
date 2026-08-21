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

const CONFIG = {
  plex: { url: "http://plex:32400", token: REDACTED },
  tmdb: { api_key: REDACTED },
  badges: { enabled: true },
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
  it("renders the redaction marker rather than anything key-shaped", async () => {
    stubConfig();
    await renderSettings();

    const dump = await screen.findByText(/plex/);
    expect(dump.textContent).toContain(REDACTED);
    expect(dump.textContent).toContain("http://plex:32400");
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
