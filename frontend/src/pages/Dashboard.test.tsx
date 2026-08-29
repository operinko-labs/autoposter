import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import type { ScheduledRun } from "../api/types";
import { formatTime } from "../format";
import { Dashboard } from "./Dashboard";

const STATUS = {
  jobs_by_state: {
    pending: 3,
    running: 1,
    deferred: 5,
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
      status: "ok",
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
      status: null,
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

/** A snapshot whose scheduled-runs table is exactly `jobs` -- everything else
 * from STATUS/EVENTS. Used by the derived-status pill tests below, which
 * care about one row's shape and nothing else in the page. */
function snapshotWithScheduledJobs(jobs: ScheduledRun[]) {
  return {
    status: { ...STATUS, scheduled_jobs: jobs },
    events: EVENTS.events,
  };
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

  it("gives the deferred tile its own class, not the one failed and parked share", async () => {
    // A deferred job is waiting, not broken -- the tile is styled --warn in
    // dashboard.css, deliberately not the --error that .stat-failed and
    // .stat-parked carry. jsdom does not compute CSS, so the class name
    // itself is what pins the rule this test guards.
    stubFetch();

    render(<Dashboard />);

    const label = await screen.findByText("deferred");
    const tile = label.parentElement;
    expect(tile).toHaveClass("stat-deferred");
    expect(tile).not.toHaveClass("stat-failed");
    expect(tile).not.toHaveClass("stat-parked");
  });

  it("keeps its timestamps on one line and both tables inside their own scroller", async () => {
    // At 500px the localized timestamp broke into five lines and crushed the
    // Run now button beside it. jsdom lays nothing out, so the assertion is
    // that the hooks are on the right nodes; the widths were checked in a
    // browser.
    stubFetch();

    render(<Dashboard />);

    const scheduled = await jobRow("collections_reconcile");
    const stamp = scheduled.getByText(formatTime(STATUS.scheduled_jobs[0].last_finished_at));
    expect(stamp).toHaveClass("cell-time");

    const tables = [...document.querySelectorAll("table")];
    expect(tables).toHaveLength(2);
    for (const table of tables) {
      expect(table.parentElement).toHaveClass("table-scroll");
    }
  });

  it("renames the scheduled-runs Result column to Status", async () => {
    stubFetch();

    render(<Dashboard />);
    await screen.findByText("collections_reconcile");

    expect(screen.getByRole("columnheader", { name: "Status" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Result" })).not.toBeInTheDocument();
  });

  describe("the derived scheduled-run status", () => {
    // A multi-minute run previously showed the *previous* run's ok/failed
    // pill for its whole duration -- indistinguishable from a scheduler that
    // never woke up. These four cover what api/snapshots.py's `_run_status`
    // can now say instead.

    it("shows a Running pill with how long the job has been going", async () => {
      // "now" fixed 5m30s after last_started_at, so formatSince floors to "5m".
      vi.useFakeTimers({ shouldAdvanceTime: true });
      vi.setSystemTime(new Date("2026-01-02T03:09:30Z"));
      stubFetch(undefined, undefined, async (_path, init) =>
        ndjsonStream(
          init!.signal as AbortSignal,
          snapshotWithScheduledJobs([
            {
              name: "prune",
              last_started_at: "2026-01-02T03:04:00Z",
              last_finished_at: null,
              last_status: "ok",
              last_detail: "previous pass",
              interval_seconds: 900,
              status: "running",
            },
          ]),
        ).response,
      );

      render(<Dashboard />);
      const row = await jobRow("prune");

      expect(row.getByText("Running")).toHaveClass("pill", "pill-running");
      expect(row.getByText("since 5m")).toBeInTheDocument();
    });

    it("advances the since label on a 60s tick, without waiting on a new snapshot", async () => {
      // The stream only pushes when the *encoded* snapshot changes, and
      // nothing about this row does between claim and finish -- so a quiet
      // 15-minute run would otherwise read "since 2m" for thirteen of those
      // minutes. Only the bounded ticker this test pins can move the label
      // when no new snapshot ever arrives.
      vi.useFakeTimers({ shouldAdvanceTime: true });
      vi.setSystemTime(new Date("2026-01-02T03:09:30Z"));
      stubFetch(undefined, undefined, async (_path, init) =>
        ndjsonStream(
          init!.signal as AbortSignal,
          snapshotWithScheduledJobs([
            {
              name: "prune",
              last_started_at: "2026-01-02T03:04:00Z",
              last_finished_at: null,
              last_status: "ok",
              last_detail: "previous pass",
              interval_seconds: 900,
              status: "running",
            },
          ]),
        ).response,
      );

      render(<Dashboard />);
      const row = await jobRow("prune");
      expect(row.getByText("since 5m")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60000);
      });

      expect(row.getByText("since 6m")).toBeInTheDocument();
    });

    it("shows an Interrupted pill explaining the run died with its process", async () => {
      stubFetch(undefined, undefined, async (_path, init) =>
        ndjsonStream(
          init!.signal as AbortSignal,
          snapshotWithScheduledJobs([
            {
              name: "prune",
              last_started_at: "2026-01-02T02:00:00Z",
              last_finished_at: null,
              last_status: "ok",
              last_detail: "previous pass",
              interval_seconds: 900,
              status: "interrupted",
            },
          ]),
        ).response,
      );

      render(<Dashboard />);
      const row = await jobRow("prune");

      const pill = row.getByText("Interrupted");
      expect(pill).toHaveClass("pill", "pill-interrupted");
      expect(pill).toHaveAttribute(
        "title",
        "started before this instance; the run died with its process",
      );
    });

    it("still renders a failed pill as before, now driven by the derived status", async () => {
      stubFetch(undefined, undefined, async (_path, init) =>
        ndjsonStream(
          init!.signal as AbortSignal,
          snapshotWithScheduledJobs([
            {
              name: "prune",
              last_started_at: "2026-01-02T03:04:00Z",
              last_finished_at: "2026-01-02T03:09:00Z",
              last_status: "failed",
              last_detail: "connection refused",
              interval_seconds: 900,
              status: "failed",
            },
          ]),
        ).response,
      );

      render(<Dashboard />);
      const row = await jobRow("prune");

      const pill = row.getByText("failed");
      expect(pill).toHaveClass("pill", "pill-failed");
      expect(pill).toHaveAttribute("title", "connection refused");
    });

    it("shows never for a job with no recorded status at all", async () => {
      stubFetch(undefined, undefined, async (_path, init) =>
        ndjsonStream(
          init!.signal as AbortSignal,
          snapshotWithScheduledJobs([
            {
              name: "prune",
              last_started_at: null,
              last_finished_at: null,
              last_status: null,
              last_detail: null,
              interval_seconds: 900,
              status: null,
            },
          ]),
        ).response,
      );

      render(<Dashboard />);
      const row = await jobRow("prune");

      expect(row.getByText("never")).toBeInTheDocument();
    });
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
