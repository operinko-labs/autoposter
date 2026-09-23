import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

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
    done_with_warnings: 4,
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

/** Two recorded runs, newest first as /api/stats/runs answers: enough for
 * RunCharts to draw both charts, and so to run its per-bar label formatter. */
const RUNS = {
  generated_at: "2026-09-05T12:40:00Z",
  runs: [
    {
      id: 2,
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
      id: 1,
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
    // RunCharts' own mount-time fetch, unrelated to any test in this file --
    // answered here so its request does not fall through to the STATUS
    // fallback below and hand the chart a body with no `runs` array.
    if (path.startsWith("/api/stats/runs")) {
      return json({ runs: [], generated_at: "2026-01-02T03:04:05Z" });
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

/** Flip `document.hidden` and announce it, as a browser does when the tab is
 * backgrounded or brought back. An own property shadows jsdom's getter;
 * afterEach deletes it again. */
function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event("visibilitychange"));
}

// RunCharts is a lazy chunk inside the page (perf spec A3) and pulls in
// recharts. Loaded once here so no single test's findBy budget pays for the
// cold transform.
beforeAll(async () => {
  await import("./RunCharts");
}, 20000);

beforeEach(() => {
  setToken(null);
});

afterEach(() => {
  vi.useRealTimers();
  Reflect.deleteProperty(document, "hidden");
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

  it("renders a tile for jobs that finished with warnings", async () => {
    stubFetch();

    render(<Dashboard />);

    expect(await screen.findByText("done_with_warnings")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
  });

  it("shows a fixed sentence, not the bytes, when the stream is not JSON", async () => {
    stubFetch(undefined, undefined, (_path, init) => {
      let controller!: ReadableStreamDefaultController<Uint8Array>;
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          controller = c;
          controller.enqueue(
            new TextEncoder().encode("<html>502 Bad Gateway from edge.internal</html>\n"),
          );
        },
      });
      (init!.signal as AbortSignal).addEventListener("abort", () => {
        controller.error(new DOMException("aborted", "AbortError"));
      });
      return Promise.resolve(
        new Response(body, { status: 200, headers: { "Content-Type": "text/html" } }),
      );
    });

    const { unmount } = render(<Dashboard />);

    expect(
      await screen.findByText("the server answered with something that was not JSON"),
    ).toBeInTheDocument();
    expect(document.body.textContent ?? "").not.toContain("edge.internal");
    unmount();
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
    // The chart's own mount fetch has happened (and answered) once its empty
    // state is on screen; it now waits for a lazy chunk first.
    await screen.findByText("No runs recorded yet.");
    expect(statValue("pending")).toBe("3");

    await act(async () => {
      push(snapshotWithPending(9));
    });

    await waitFor(() => expect(statValue("pending")).toBe("9"));
    // The whole point of the stream: the page no longer re-reads /api/status
    // or /api/events, per tick or at all. RunCharts' two mount fetches (the
    // recent page and the counted page) are the only other requests,
    // order-independent since child effects and the stream's own connect
    // effect are not sequenced against each other.
    expect(fetchMock.mock.calls.map(([path]) => path).sort()).toEqual(
      [
        "/api/dashboard/stream",
        "/api/stats/runs?limit=50",
        "/api/stats/runs?limit=50&counted=true",
      ].sort(),
    );
  });

  it("leaves the run charts alone when a snapshot does not change run history", async () => {
    // Perf spec A4. Every stream tick re-rendered the page and, with it,
    // RunCharts -- which re-derived both series, one toLocaleTimeString per
    // bar, from a history that had not moved. The formatter is the probe: it
    // runs only when the series are derived, and nothing else here calls it.
    let push!: (value: unknown) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path === "/api/dashboard/stream") {
          const stream = ndjsonStream(init!.signal as AbortSignal, SNAPSHOT);
          push = stream.push;
          return stream.response;
        }
        if (path.startsWith("/api/stats/runs")) return json(RUNS);
        return json({ detail: `nothing declared for ${path}` }, 500);
      }),
    );
    const labels = vi.spyOn(Date.prototype, "toLocaleTimeString");
    try {
      render(<Dashboard />);
      await screen.findByText("Run duration");
      await screen.findByText("collections_reconcile");
      const derived = labels.mock.calls.length;
      expect(derived).toBeGreaterThan(0);

      await act(async () => {
        push(snapshotWithPending(9));
      });
      await waitFor(() => expect(statValue("pending")).toBe("9"));

      expect(labels.mock.calls.length).toBe(derived);
    } finally {
      labels.mockRestore();
    }
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
    // RunCharts' one mount fetch, counted below, has landed.
    await screen.findByText("No runs recorded yet.");

    // The server ended the stream -- a restart, a proxy timeout.
    await act(async () => {
      close();
    });

    expect(await screen.findByText("reconnecting…")).toBeInTheDocument();
    // Blanking the page while disconnected would be worse than showing data a
    // few seconds old, so the last snapshot stays put.
    expect(statValue("pending")).toBe("3");
    expect(screen.getByText(/4 workers/)).toBeInTheDocument();
    // The stream's own connect plus RunCharts' two mount fetches.
    expect(fetchMock).toHaveBeenCalledTimes(3);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(fetchMock).toHaveBeenCalledTimes(4);
    await waitFor(() => expect(statValue("pending")).toBe("9"));
    expect(screen.getByText("live")).toBeInTheDocument();
  });

  it("aborts the stream on unmount and opens nothing afterwards", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = stubFetch();

    const { unmount } = render(<Dashboard />);
    await screen.findByText("collections_reconcile");
    // The stream's own connect plus RunCharts' two mount fetches --
    // RunCharts' own effect is not sequenced against the stream's, so it can
    // lag the text this page just rendered by a tick.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    // Found by path rather than assumed to be calls[0]: RunCharts' own effect
    // and the stream's connect effect are not sequenced against each other.
    const streamCall = fetchMock.mock.calls.find(([path]) => path === "/api/dashboard/stream");
    const signal = (streamCall![1] as RequestInit).signal;

    unmount();

    // Without the abort the read hangs on for the life of the tab, and
    // without the aborted-check the ended read would reconnect it.
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("closes the stream while the tab is hidden and reconnects when it is shown", async () => {
    // Perf spec A8: a background tab held a stream open, and the server kept
    // polling the database every two seconds for a page nobody could see.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const signals: AbortSignal[] = [];
    stubFetch(undefined, undefined, async (_path, init) => {
      signals.push(init!.signal as AbortSignal);
      return ndjsonStream(
        init!.signal as AbortSignal,
        signals.length === 1 ? SNAPSHOT : snapshotWithPending(9),
      ).response;
    });

    render(<Dashboard />);
    await screen.findByText("collections_reconcile");

    await act(async () => {
      setHidden(true);
    });
    expect(signals[0].aborted).toBe(true);
    // Far past the three-second reconnect: a hidden tab opens nothing.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(signals).toHaveLength(1);
    // The last snapshot stays on screen meanwhile.
    expect(statValue("pending")).toBe("3");

    await act(async () => {
      setHidden(false);
    });
    await waitFor(() => expect(signals).toHaveLength(2));
    await waitFor(() => expect(statValue("pending")).toBe("9"));
  });

  it("opens no stream while mounted in a hidden tab, and connects once shown", async () => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    const fetchMock = stubFetch();

    render(<Dashboard />);
    await act(async () => {});
    expect(fetchMock.mock.calls.some(([path]) => path === "/api/dashboard/stream")).toBe(false);

    await act(async () => {
      setHidden(false);
    });
    expect(await screen.findByText("collections_reconcile")).toBeInTheDocument();
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
