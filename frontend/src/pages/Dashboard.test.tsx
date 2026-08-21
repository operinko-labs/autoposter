import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Dashboard } from "./Dashboard";

const STATUS = {
  jobs_by_state: {
    pending: 3,
    running: 1,
    done: 412,
    failed: 2,
    parked: 7,
    dismissed: 0,
  },
  workers: 4,
  processed_last_24h: 96,
  scheduled_jobs: [
    {
      name: "library_scan",
      last_started_at: "2026-01-02T03:04:05Z",
      last_finished_at: "2026-01-02T03:09:05Z",
      last_status: "ok",
      last_detail: "nothing to do",
    },
  ],
};

const EVENTS = {
  events: [
    {
      source: "plex",
      event_type: "library.new",
      outcome: "queued",
      received_at: "2026-01-02T03:04:05Z",
    },
  ],
};

function stubFetch() {
  const fetchMock = vi.fn(
    async (path: string) =>
      new Response(
        JSON.stringify(path.startsWith("/api/events") ? EVENTS : STATUS),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  setToken(null);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Dashboard", () => {
  it("renders the job counts, worker count and activity from the API", async () => {
    stubFetch();

    render(<Dashboard />);

    // One card per state, labelled and carrying the server's number. The
    // label lookup is what pins the value to the right state -- a grid that
    // rendered every count against the wrong label would still contain "7".
    for (const [state, count] of Object.entries(STATUS.jobs_by_state)) {
      const label = await screen.findByText(state);
      expect(label.parentElement).toHaveTextContent(String(count));
    }

    expect(screen.getByText(/4 workers/)).toBeInTheDocument();
    expect(screen.getByText(/96 processed in 24h/)).toBeInTheDocument();

    expect(screen.getByText("library_scan")).toBeInTheDocument();
    expect(screen.getByText("plex")).toBeInTheDocument();
    expect(screen.getByText("library.new")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("polls while mounted and stops when unmounted", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = stubFetch();

    const { unmount } = render(<Dashboard />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));

    // /api/status and /api/events per tick.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(4);

    unmount();

    // The interval has to be cleared, or a page the user navigated away from
    // keeps a request every five seconds running for the life of the tab.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("shows the failure rather than an endless spinner", async () => {
    // A fresh Response per call: a body can only be read once, and the page
    // makes two requests per poll.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "database is down" }), {
            status: 500,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );

    render(<Dashboard />);

    expect(await screen.findByText("database is down")).toBeInTheDocument();
  });
});
