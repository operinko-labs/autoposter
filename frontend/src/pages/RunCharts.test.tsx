import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import type { RunsResponse } from "../api/types";
import { RunCharts } from "./RunCharts";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const RUNS: RunsResponse = {
  generated_at: "2026-09-05T12:40:00Z",
  runs: [
    {
      id: 3,
      kind: "full_pass",
      name: "full_pass",
      started_at: "2026-09-05T09:00:00Z",
      finished_at: "2026-09-05T12:31:04Z",
      status: "ok",
      duration_seconds: 12664,
      rendered: { poster: 12, season_poster: 0, background: 3, title_card: 0 },
      processed: 15940,
      failed: 12,
      deferred: 8,
    },
    {
      id: 2,
      kind: "scheduled",
      name: "plex_prune",
      started_at: "2026-09-05T08:00:00Z",
      finished_at: "2026-09-05T08:00:42Z",
      status: "ok",
      duration_seconds: 42,
      rendered: null,
      processed: null,
      failed: null,
      deferred: null,
    },
  ],
};

describe("RunCharts", () => {
  beforeEach(() => {
    setToken("test-token");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setToken(null);
  });

  it("reads the run history once on mount and draws both charts", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(RUNS));

    const { container } = render(<RunCharts />);

    await waitFor(() => expect(screen.getByText("Run duration")).toBeInTheDocument());
    expect(screen.getByText("Processed and failed per run")).toBeInTheDocument();
    // Two charts, two chart canvases. Recharts is given explicit pixel
    // dimensions (see RunCharts.tsx) precisely so it renders in jsdom rather
    // than measuring a container that has no size here. Scoped to
    // `role="application"` rather than every <svg>: Recharts 3.10.1's <Legend>
    // renders each series' swatch as its own tiny <svg> too (one per Bar in
    // the stacked chart), which is decoration, not a chart.
    expect(container.querySelectorAll('svg[role="application"]').length).toBe(2);
    // Two requests, both on mount -- this page is NOT on the dashboard stream.
    // The recent page feeds the duration chart; the counted page feeds the
    // counts chart, so a busy scheduled job cannot push every counted run out
    // of view.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.map((call) => String(call[0])).sort()).toEqual([
      "/api/stats/runs?limit=50",
      "/api/stats/runs?limit=50&counted=true",
    ]);
  });

  it("draws the counts chart from counted runs even when the recent page has none", async () => {
    // Production shape: the newest fifty runs are all a fifteen-minute
    // scheduled job, and the only full pass sits far behind them.
    const recent: RunsResponse = {
      generated_at: RUNS.generated_at,
      runs: Array.from({ length: 50 }, (_, index) => ({
        ...RUNS.runs[1],
        id: 1000 - index,
        name: "pending_deliveries",
      })),
    };
    const counted: RunsResponse = { generated_at: RUNS.generated_at, runs: [RUNS.runs[0]] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      json(String(input).includes("counted=true") ? counted : recent),
    );

    const { container } = render(<RunCharts />);

    await waitFor(() => expect(screen.getByText("Run duration")).toBeInTheDocument());
    expect(screen.queryByText("No full pass has completed yet.")).not.toBeInTheDocument();
    expect(container.querySelectorAll('svg[role="application"]').length).toBe(2);
  });

  it("says so rather than drawing an empty chart when there is no history", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      json({ runs: [], generated_at: "2026-09-05T12:40:00Z" }),
    );

    const { container } = render(<RunCharts />);

    await waitFor(() => expect(screen.getByText("No runs recorded yet.")).toBeInTheDocument());
    expect(container.querySelectorAll("svg").length).toBe(0);
  });

  it("plots only attributed runs in the counts chart, never a null as a zero", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => json(RUNS));

    render(<RunCharts />);

    await waitFor(() => expect(screen.getByText("Run duration")).toBeInTheDocument());
    // Both runs have a duration; only the full pass has counts. The caption is
    // what tells the operator that, and it is asserted because a chart that
    // silently drops half its bars is worse than one that says why.
    expect(
      screen.getByText(/counts are recorded for full passes only/i),
    ).toBeInTheDocument();
  });
});
