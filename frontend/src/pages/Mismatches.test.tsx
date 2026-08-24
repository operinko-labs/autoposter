import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Mismatches } from "./Mismatches";

const MISMATCHED = {
  service: "radarr",
  kind: "movie",
  path: "/mnt/media/Movies/Dune (2021)",
  library: "Movies",
  rating_key: "1",
  plex_title: "Dune",
  arr_title: "Dune (1984)",
  year: 2021,
  arr_ids: { tmdb: "841", imdb: "tt1160419" },
  plex_ids: { tmdb: "438631", imdb: "tt1160419" },
  differing: ["tmdb"],
};

const ARR_ONLY = {
  ...MISMATCHED,
  service: "sonarr",
  kind: "series",
  path: "/mnt/media/TV/Andor",
  library: null,
  rating_key: null,
  plex_title: null,
  arr_title: "Andor",
  arr_ids: { tvdb: "393199" },
  plex_ids: {},
  differing: [],
};

const PLEX_ONLY = {
  ...MISMATCHED,
  path: "/mnt/media/Movies/Sinners (2025)",
  rating_key: "2",
  plex_title: "Sinners",
  arr_title: null,
  arr_ids: {},
  plex_ids: { tmdb: "1233413" },
  differing: [],
};

function body(overrides: Record<string, unknown> = {}) {
  const base = {
    mismatched: [],
    arr_only: [],
    plex_only: [],
    counts: { mismatched: 0, arr_only: 0, plex_only: 0 },
    total: 0,
    limit: 500,
    skipped: [],
    unmapped: 0,
    ...overrides,
  };
  return new Response(JSON.stringify(base), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function everything() {
  return body({
    mismatched: [MISMATCHED],
    arr_only: [ARR_ONLY],
    plex_only: [PLEX_ONLY],
    counts: { mismatched: 1, arr_only: 1, plex_only: 1 },
    total: 3,
  });
}

beforeEach(() => {
  setToken(null);
});

describe("Mismatches", () => {
  it("scans on the button rather than on mount", async () => {
    // The endpoint reads both services in full and walks every Plex section.
    // A page that fetched on mount would run that every time it was opened,
    // and polling it would run it forever.
    const fetchMock = vi.fn().mockResolvedValue(everything());
    vi.stubGlobal("fetch", fetchMock);

    render(<Mismatches />);

    expect(await screen.findByText("Nothing scanned yet.")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(await screen.findByText("Dune")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/id-mismatches");
  });

  it("renders the three groups with their counts", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(everything()));

    render(<Mismatches />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(await screen.findByText("Mismatched ids")).toBeInTheDocument();
    expect(screen.getByText("In Radarr/Sonarr only")).toBeInTheDocument();
    expect(screen.getByText("In Plex only")).toBeInTheDocument();
    // The rows themselves, one per group.
    expect(screen.getByText("Dune")).toBeInTheDocument();
    expect(screen.getByText("Andor")).toBeInTheDocument();
    expect(screen.getByText("Sinners")).toBeInTheDocument();
    expect(screen.getByText("3 rows")).toBeInTheDocument();
  });

  it("marks the disagreeing id on both sides and leaves the agreeing one plain", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        body({
          mismatched: [MISMATCHED],
          counts: { mismatched: 1, arr_only: 0, plex_only: 0 },
          total: 1,
        }),
      ),
    );

    render(<Mismatches />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    const wrong = await screen.findByText("841");
    const right = screen.getByText("438631");
    expect(wrong.parentElement).toHaveClass("differs");
    expect(right.parentElement).toHaveClass("differs");
    // The id that agrees is rendered too -- it is how an operator tells which
    // of the two records is the wrong one -- and must not be highlighted.
    const agreeing = screen.getAllByText("tt1160419");
    expect(agreeing).toHaveLength(2);
    for (const chip of agreeing) expect(chip.parentElement).not.toHaveClass("differs");
  });

  it("shows an empty state when the scan found nothing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(body()));

    render(<Mismatches />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(await screen.findByText("No mismatches found.")).toBeInTheDocument();
  });

  it("names a service that was not scanned", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(body({ skipped: ["sonarr"] })));

    render(<Mismatches />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    // Otherwise an empty result reads as "Sonarr is clean" when Sonarr was
    // never asked.
    expect(await screen.findByText(/Not scanned: sonarr/)).toBeInTheDocument();
  });

  it("disables the button while the scan is running", async () => {
    let release: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending));

    render(<Mismatches />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    const button = await screen.findByRole("button", { name: "Scanning…" });
    expect(button).toBeDisabled();

    release(body());
    await waitFor(() => expect(screen.getByRole("button", { name: "Scan" })).toBeEnabled());
  });

  it("reports a failed scan without losing the button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "radarr did not answer" }), {
          status: 502,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    render(<Mismatches />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    expect(await screen.findByText(/radarr did not answer/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Scan" })).toBeEnabled();
  });

  it("contains each table and wraps the path", async () => {
    // The mobile conventions, as on Jobs: a full path is long and arbitrary,
    // and unwrapped it widens the table past the content column. jsdom
    // computes no layout, so what is assertable is that the hooks the CSS
    // hangs off are on the right nodes.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(everything()));

    render(<Mismatches />);
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    const path = await screen.findByText("/mnt/media/Movies/Dune (2021)");
    expect(path).toHaveClass("cell-wrap");
    expect(path.closest("table")?.parentElement).toHaveClass("table-scroll");
    expect(screen.getByText("438631").closest(".mismatch-ids")).not.toBeNull();
  });
});
