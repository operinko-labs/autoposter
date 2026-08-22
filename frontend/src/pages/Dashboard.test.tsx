import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(fullPass?: (path: string, init?: RequestInit) => Promise<Response>) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/full-pass" && fullPass) return fullPass(path, init);
    return json(path.startsWith("/api/events") ? EVENTS : STATUS);
  });
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
    // label lookup is what pins the value to the right state, and the value
    // is read from `.stat-value` and compared exactly -- `toHaveTextContent`
    // on the whole card is a substring match, so a card showing "412" would
    // also satisfy the assertion for the state whose count is "2".
    for (const [state, count] of Object.entries(STATUS.jobs_by_state)) {
      const label = await screen.findByText(state);
      const value = label.parentElement?.querySelector(".stat-value");
      expect(value?.textContent).toBe(String(count));
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

  it("runs a full pass and shows the server's outcome, not an optimistic one", async () => {
    // Everything already pending: an optimistic UI would claim the library
    // was queued, but the truthful outcome is zero queued. The exact string
    // is asserted so a substring like "12" alone cannot satisfy it.
    const fetchMock = stubFetch(async () => json({ total: 12, queued: 0, skipped: 12 }));

    render(<Dashboard />);
    fireEvent.click(await screen.findByRole("button", { name: "Run full pass" }));

    expect(
      await screen.findByText("Full pass: 0 queued, 12 already queued (12 items).")
    ).toBeInTheDocument();

    const passCall = fetchMock.mock.calls.find(([path]) => path === "/api/full-pass");
    expect(passCall).toBeDefined();
    expect((passCall![1] as RequestInit).method).toBe("POST");
  });

  it("disables the button while the pass request is in flight", async () => {
    let resolvePass!: (response: Response) => void;
    stubFetch(() => new Promise<Response>((resolve) => (resolvePass = resolve)));

    render(<Dashboard />);
    fireEvent.click(await screen.findByRole("button", { name: "Run full pass" }));

    const running = await screen.findByRole("button", { name: "Running…" });
    expect(running).toBeDisabled();

    await act(async () => {
      resolvePass(json({ total: 3, queued: 3, skipped: 0 }));
    });

    const button = await screen.findByRole("button", { name: "Run full pass" });
    expect(button).toBeEnabled();
    expect(screen.getByText("Full pass: 3 queued, 0 already queued (3 items).")).toBeInTheDocument();
  });

  it("surfaces a failed pass through the error display and claims no outcome", async () => {
    stubFetch(async () => json({ detail: "the queue is unreachable" }, 500));

    render(<Dashboard />);
    fireEvent.click(await screen.findByRole("button", { name: "Run full pass" }));

    expect(await screen.findByText("the queue is unreachable")).toBeInTheDocument();
    expect(screen.queryByText(/Full pass:/)).not.toBeInTheDocument();
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
