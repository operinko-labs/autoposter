import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
      name: "collections_reconcile",
      last_started_at: "2026-01-02T03:04:05Z",
      last_finished_at: "2026-01-02T03:09:05Z",
      last_status: "ok",
      last_detail: "nothing to do",
      interval_seconds: 900,
    },
    // Registration is config-conditional: this row survives from a
    // deployment that ran the job, but the running scheduler knows no
    // interval for it, so it is not registered here.
    {
      name: "arr_sync",
      last_started_at: null,
      last_finished_at: null,
      last_status: null,
      last_detail: null,
      interval_seconds: null,
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

/** One line of GET /api/dashboard/stream: the same two payloads the page used
 * to fetch separately, in one object. */
const SNAPSHOT = { status: STATUS, events: EVENTS.events };

function snapshotWithPending(pending: number) {
  return {
    status: { ...STATUS, jobs_by_state: { ...STATUS.jobs_by_state, pending } },
    events: EVENTS.events,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A streamed NDJSON body, per the helper in Logs.test.tsx. The stream stays
 * open until the test closes it (a closed stream makes the page schedule a
 * reconnect), and it errors its own controller when the fetch's AbortSignal
 * fires, matching a real fetch body -- without that, `apiFetchNdjson`'s
 * pending `reader.read()` never settles and unmount leaves it dangling. */
function ndjsonStream(signal: AbortSignal, ...values: unknown[]) {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
      for (const value of values) {
        controller.enqueue(encoder.encode(JSON.stringify(value) + "\n"));
      }
    },
  });
  signal.addEventListener("abort", () => {
    controller.error(new DOMException("aborted", "AbortError"));
  });
  return {
    response: new Response(body, {
      status: 200,
      headers: { "Content-Type": "application/x-ndjson" },
    }),
    push(value: unknown) {
      controller.enqueue(encoder.encode(JSON.stringify(value) + "\n"));
    },
    close() {
      controller.close();
    },
  };
}

type Handler = (path: string, init?: RequestInit) => Promise<Response>;

function stubFetch(fullPass?: Handler, run?: Handler, stream?: Handler) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/dashboard/stream") {
      if (stream) return stream(path, init);
      return ndjsonStream(init!.signal as AbortSignal, SNAPSHOT).response;
    }
    if (path === "/api/full-pass" && fullPass) return fullPass(path, init);
    if (path.startsWith("/api/scheduled-runs/")) {
      return run ? run(path, init) : json({ status: "requested", poll_seconds: 60 });
    }
    return json(path.startsWith("/api/events") ? EVENTS : STATUS);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The number on the card for `state`, read the same way the render test reads
 * it: via the label, so the value is pinned to the right card. */
function statValue(state: string): string | undefined {
  return screen.getByText(state).parentElement?.querySelector(".stat-value")?.textContent ?? undefined;
}

/** The scheduled-runs row for `name`, scoped so a second row's identically
 * labelled button cannot satisfy an assertion about this one. */
async function jobRow(name: string) {
  const cell = await screen.findByText(name);
  const row = cell.closest("tr");
  expect(row).not.toBeNull();
  return within(row!);
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

    expect(screen.getByText("collections_reconcile")).toBeInTheDocument();
    expect(screen.getByText("plex")).toBeInTheDocument();
    expect(screen.getByText("library.new")).toBeInTheDocument();
    expect(screen.getByText("queued")).toBeInTheDocument();
  });

  it("updates a count in place from a later snapshot, fetching nothing else", async () => {
    let push!: (value: unknown) => void;
    const fetchMock = stubFetch(undefined, undefined, async (_path, init) => {
      const stream = ndjsonStream(init!.signal as AbortSignal, SNAPSHOT);
      push = stream.push;
      return stream.response;
    });

    render(<Dashboard />);
    await screen.findByText("collections_reconcile");
    expect(statValue("pending")).toBe("3");

    await act(async () => {
      push(snapshotWithPending(9));
    });

    await waitFor(() => expect(statValue("pending")).toBe("9"));
    // The whole point of the stream: the page no longer re-reads /api/status
    // or /api/events, per tick or at all.
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(["/api/dashboard/stream"]);
  });

  it("ignores heartbeat lines rather than treating them as snapshots", async () => {
    stubFetch(undefined, undefined, async (_path, init) =>
      ndjsonStream(init!.signal as AbortSignal, SNAPSHOT, { heartbeat: true }).response,
    );

    render(<Dashboard />);

    // A heartbeat that got past the guard would be stored as the snapshot and
    // crash the render (status.jobs_by_state is undefined) rather than fail an
    // assertion, so also assert no trace of it reached the DOM.
    expect(await screen.findByText("collections_reconcile")).toBeInTheDocument();
    await waitFor(() => expect(statValue("pending")).toBe("3"));
    expect(document.body.textContent ?? "").not.toContain("heartbeat");
  });

  it("keeps the last snapshot rendered and reconnects three seconds after a drop", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let connects = 0;
    let close!: () => void;
    const fetchMock = stubFetch(undefined, undefined, async (_path, init) => {
      connects += 1;
      const stream = ndjsonStream(
        init!.signal as AbortSignal,
        connects === 1 ? SNAPSHOT : snapshotWithPending(9),
      );
      close = stream.close;
      return stream.response;
    });

    render(<Dashboard />);
    await screen.findByText("collections_reconcile");

    // The server ended the stream -- a restart, a proxy timeout.
    await act(async () => {
      close();
    });

    expect(await screen.findByText("reconnecting…")).toBeInTheDocument();
    // Blanking the page while disconnected would be worse than showing data a
    // few seconds old, so the last snapshot stays put.
    expect(statValue("pending")).toBe("3");
    expect(screen.getByText(/4 workers/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(statValue("pending")).toBe("9"));
    expect(screen.getByText("live")).toBeInTheDocument();
  });

  it("aborts the stream on unmount and opens nothing afterwards", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = stubFetch();

    const { unmount } = render(<Dashboard />);
    await screen.findByText("collections_reconcile");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const signal = (fetchMock.mock.calls[0][1] as RequestInit).signal;

    unmount();

    // Without the abort the read hangs on for the life of the tab, and
    // without the aborted-check the ended read would reconnect it.
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
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

  it("requests a run for one job and quotes the poll interval", async () => {
    const fetchMock = stubFetch(undefined, async () =>
      json({ status: "requested", poll_seconds: 30 })
    );

    render(<Dashboard />);
    const row = await jobRow("collections_reconcile");
    fireEvent.click(row.getByRole("button", { name: "Run now" }));

    // The endpoint only marks the row due -- nothing has started, and the
    // note must not imply it has. It also nulls last_started_at server-side,
    // so the row is briefly "never started"; that is what this note covers.
    expect(await row.findByText("requested — picks up within 30s")).toBeInTheDocument();

    const call = fetchMock.mock.calls.find(([path]) => path.startsWith("/api/scheduled-runs/"));
    expect(call?.[0]).toBe("/api/scheduled-runs/collections_reconcile/run");
    expect((call?.[1] as RequestInit).method).toBe("POST");
  });

  it("disables Run now for a job this deployment did not register", async () => {
    stubFetch();

    render(<Dashboard />);

    const registered = await jobRow("collections_reconcile");
    expect(registered.getByRole("button", { name: "Run now" })).toBeEnabled();

    // A null interval means the scheduler has no such job. Marking the row
    // due would leave a permanently-due row nothing ever claims.
    const absent = await jobRow("arr_sync");
    const button = absent.getByRole("button", { name: "Run now" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "not scheduled in this deployment's config");
  });

  it("disables the button while the run request is in flight", async () => {
    let resolveRun!: (response: Response) => void;
    stubFetch(undefined, () => new Promise<Response>((resolve) => (resolveRun = resolve)));

    render(<Dashboard />);
    const row = await jobRow("collections_reconcile");
    fireEvent.click(row.getByRole("button", { name: "Run now" }));

    // Not idempotent against an in-flight run: a second press queues a
    // second copy of the pass.
    const busy = await row.findByRole("button", { name: "Requesting…" });
    expect(busy).toBeDisabled();

    await act(async () => {
      resolveRun(json({ status: "requested", poll_seconds: 60 }));
    });

    expect(await row.findByRole("button", { name: "Run now" })).toBeEnabled();
  });

  it("surfaces a failed run request in the row it belongs to", async () => {
    stubFetch(undefined, async () => json({ detail: "unknown scheduled job" }, 404));

    render(<Dashboard />);
    const row = await jobRow("collections_reconcile");
    fireEvent.click(row.getByRole("button", { name: "Run now" }));

    expect(await row.findByText("unknown scheduled job")).toBeInTheDocument();
    expect(screen.queryByText(/picks up within/)).not.toBeInTheDocument();
  });

  it("shows the failure rather than an endless spinner", async () => {
    // A fresh Response per call: a body can only be read once, and the failed
    // stream is retried.
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
