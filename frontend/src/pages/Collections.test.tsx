import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { formatTime } from "../format";
import { Collections } from "./Collections";

/** Three rows covering the three states the stats can be in. `Marvel
 * Chronological` has real numbers, `Top Rated` is smart so its stats are
 * permanently null, and `Comedy Gold` has been reconciled and found nothing to
 * change -- zeroes, which must NOT render like the nulls do. */
const COLLECTIONS = {
  collections: [
    {
      id: 1,
      library: "Movies",
      title: "Marvel Chronological",
      kind: "manual",
      member_count: 34,
      last_added: 3,
      last_removed: 1,
      last_reconciled_at: "2026-03-04T05:06:07Z",
    },
    {
      id: 2,
      library: "Movies",
      title: "Top Rated",
      kind: "smart",
      member_count: null,
      last_added: null,
      last_removed: null,
      last_reconciled_at: null,
    },
    {
      id: 3,
      library: "Shows",
      title: "Comedy Gold",
      kind: "manual",
      member_count: 0,
      last_added: 0,
      last_removed: 0,
      last_reconciled_at: "2026-03-04T05:06:07Z",
    },
  ],
};

function status(overrides: Record<string, unknown> = {}) {
  return {
    jobs_by_state: { pending: 0, running: 0, done: 0, failed: 0, parked: 0, dismissed: 0 },
    workers: 2,
    processed_last_24h: 0,
    scheduled_jobs: [
      {
        name: "collections_reconcile",
        last_started_at: "2026-01-02T03:04:05Z",
        last_finished_at: "2026-01-02T03:09:05Z",
        last_status: "ok",
        last_detail: "3 updated",
        interval_seconds: 3600,
        ...overrides,
      },
    ],
  };
}

function json(body: unknown, code = 200): Response {
  return new Response(JSON.stringify(body), {
    status: code,
    headers: { "Content-Type": "application/json" },
  });
}

/** A fixture is either a fixed body or a function called per request, so a
 * test can let the server's answer change between polls -- which is the whole
 * point of the completion watch. */
type Supplied = unknown | (() => unknown);

function supply(supplied: Supplied, fallback: unknown): unknown {
  if (typeof supplied === "function") return (supplied as () => unknown)();
  return supplied ?? fallback;
}

interface StubOptions {
  collections?: Supplied;
  status?: Supplied;
  run?: (path: string, init?: RequestInit) => Promise<Response>;
}

function stubFetch(options: StubOptions = {}) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.startsWith("/api/scheduled-runs/") && options.run) return options.run(path, init);
    if (path.startsWith("/api/scheduled-runs/")) return json({ status: "requested", poll_seconds: 60 });
    if (path === "/api/status") return json(supply(options.status, status()));
    return json(supply(options.collections, COLLECTIONS));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function callsTo(fetchMock: ReturnType<typeof stubFetch>, path: string) {
  return fetchMock.mock.calls.filter(([called]) => called === path).length;
}

async function rowFor(title: string) {
  const cell = await screen.findByText(title);
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

describe("Collections", () => {
  it("shows the member count and the last diff in the exact +N −M format", async () => {
    stubFetch();

    render(<Collections />);

    const row = await rowFor("Marvel Chronological");
    expect(row.getByText("34")).toBeInTheDocument();

    // Exact text, not a substring: "+3 −1" and "+1 −3" are both plausible
    // renderings of the same two numbers and only one of them is true.
    const diff = row.getByText("+3 −1");
    // The reconcile timestamp rides along as the title so the numbers are not
    // read as current when they are months old. It is last_reconciled_at, not
    // the scheduled run's last_finished_at -- the fixture gives them
    // different values so a mix-up cannot pass.
    expect(diff).toHaveAttribute("title", formatTime("2026-03-04T05:06:07Z"));
  });

  it("gives the title cell a width to hold and the table its own scroller", async () => {
    // The title was the only flexible column against Library/Kind/Members/Last
    // diff, so at 500px it collapsed to one word per line. The floor is CSS;
    // what jsdom can pin is that it is applied to the title cell and not, say,
    // to the library cell beside it.
    stubFetch();

    render(<Collections />);

    const title = await screen.findByText("Marvel Chronological");
    expect(title).toHaveClass("cell-title");
    expect(title.closest("table")?.parentElement).toHaveClass("table-scroll");
  });

  it("renders null stats as — and zero stats as 0, which are different states", async () => {
    stubFetch();

    render(<Collections />);

    // Smart collection: Plex evaluates the filter live, so there is no member
    // count and no diff to report. Rendering 0 here would claim the
    // collection is empty and that nothing changed, both untrue.
    const smart = await rowFor("Top Rated");
    expect(smart.getAllByText("—")).toHaveLength(2);
    expect(smart.queryByText("0")).not.toBeInTheDocument();
    expect(smart.queryByText("+0 −0")).not.toBeInTheDocument();

    // Reconciled and genuinely unchanged: this one really is 0/0.
    const quiet = await rowFor("Comedy Gold");
    expect(quiet.getByText("0")).toBeInTheDocument();
    expect(quiet.getByText("+0 −0")).toBeInTheDocument();
    expect(quiet.queryByText("—")).not.toBeInTheDocument();
  });

  it("computes the next refresh from last_started_at plus interval_seconds", async () => {
    stubFetch();

    render(<Collections />);

    // 03:04:05Z started + 3600s interval == 04:04:05Z. The server sends no
    // next-run time; getting the arithmetic backwards would show a refresh
    // that already happened.
    expect(
      await screen.findByText(`Next refresh: ${formatTime("2026-01-02T04:04:05Z")}`)
    ).toBeInTheDocument();
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("cannot compute a next refresh when the job is not registered here", async () => {
    stubFetch({ status: status({ interval_seconds: null }) });

    render(<Collections />);

    // Null interval means this deployment never registered the job, so the
    // cadence is unknowable -- not zero, not the default.
    expect(await screen.findByText("Next refresh: —")).toBeInTheDocument();
  });

  it("disables Diff now when the job is not scheduled in this deployment", async () => {
    stubFetch({ status: status({ interval_seconds: null }) });

    render(<Collections />);

    const button = await screen.findByRole("button", { name: "Diff now" });
    // Requesting a run of an unregistered job seeds a permanently-due row
    // that no scheduler will ever claim.
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "not scheduled in this deployment's config");
  });

  it("requests a reconcile and quotes the poll interval rather than claiming a run", async () => {
    const fetchMock = stubFetch({
      run: async () => json({ status: "requested", poll_seconds: 45 }),
    });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));

    // "requested", never "started": the endpoint only marks the row due.
    expect(await screen.findByText("requested — picks up within 45s")).toBeInTheDocument();

    const call = fetchMock.mock.calls.find(([path]) =>
      path.startsWith("/api/scheduled-runs/")
    );
    expect(call?.[0]).toBe("/api/scheduled-runs/collections_reconcile/run");
    expect((call?.[1] as RequestInit).method).toBe("POST");

    // Status is re-read afterwards rather than patched locally.
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter(([path]) => path === "/api/status")).toHaveLength(2)
    );
  });

  it("disables Diff now while the request is in flight", async () => {
    let resolveRun!: (response: Response) => void;
    stubFetch({ run: () => new Promise<Response>((resolve) => (resolveRun = resolve)) });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));

    // Run-now is not idempotent against an in-flight run: a second press
    // starts a second copy of the pass on the next poll.
    const busy = await screen.findByRole("button", { name: "Requesting…" });
    expect(busy).toBeDisabled();

    await act(async () => {
      resolveRun(json({ status: "requested", poll_seconds: 60 }));
    });

    expect(await screen.findByRole("button", { name: "Diff now" })).toBeEnabled();
  });

  it("surfaces a failed request and claims nothing was requested", async () => {
    stubFetch({ run: async () => json({ detail: "scheduler table is locked" }, 500) });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));

    expect(await screen.findByText("scheduler table is locked")).toBeInTheDocument();
    expect(screen.queryByText(/picks up within/)).not.toBeInTheDocument();
  });

  it("keeps polling status without re-reading collections while the pass has not finished", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // last_finished_at never moves: the requested pass has not landed yet.
    const fetchMock = stubFetch();

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));
    expect(await screen.findByText("requested — picks up within 60s")).toBeInTheDocument();

    const statusCalls = callsTo(fetchMock, "/api/status");
    expect(callsTo(fetchMock, "/api/collections")).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });

    // Two ticks of the watch: status is re-read each time, but the collection
    // numbers are still the pre-run ones, so re-reading them would be churn
    // that shows nothing new.
    expect(callsTo(fetchMock, "/api/status")).toBe(statusCalls + 2);
    expect(callsTo(fetchMock, "/api/collections")).toBe(1);
    expect(screen.getByText("requested — picks up within 60s")).toBeInTheDocument();
    expect((await rowFor("Marvel Chronological")).getByText("+3 −1")).toBeInTheDocument();
  });

  it("re-reads collections in place and drops the note once last_finished_at advances", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let finished = false;
    const fetchMock = stubFetch({
      status: () =>
        finished
          ? status({ last_finished_at: "2026-01-02T09:09:05Z", last_detail: "7 updated" })
          : status(),
      collections: () =>
        finished
          ? {
              collections: [
                {
                  ...COLLECTIONS.collections[0],
                  member_count: 40,
                  last_added: 6,
                  last_removed: 0,
                },
                ...COLLECTIONS.collections.slice(1),
              ],
            }
          : COLLECTIONS,
    });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));
    expect(await screen.findByText("requested — picks up within 60s")).toBeInTheDocument();

    finished = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });

    // The point of the whole loop: the new numbers appear without a reload.
    const row = await rowFor("Marvel Chronological");
    expect(row.getByText("40")).toBeInTheDocument();
    expect(row.getByText("+6 −0")).toBeInTheDocument();
    expect(row.queryByText("+3 −1")).not.toBeInTheDocument();
    // The request is no longer outstanding, so the note must go.
    expect(screen.queryByText(/picks up within/)).not.toBeInTheDocument();
    expect(screen.getByText(`Last refresh: ${formatTime("2026-01-02T09:09:05Z")}`)).toBeInTheDocument();

    // And the watch stops: it exists only between an accepted request and its
    // completion, never as a page-wide poll.
    const settled = fetchMock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock.mock.calls.length).toBe(settled);
  });

  it("captures the completion baseline from the post-request status read, not the stale page-load state", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // Simulates a tab left open past interval_seconds: an autonomous pass
    // finishes (T1 -> T2) while the page sits idle, so the page-load status
    // is stale by the time Diff now is pressed. The post-POST status read
    // (call 2) already carries T2 -- that has to be the watch's baseline, or
    // the first tick (which still reports T2, since the requested pass has
    // not landed yet) is mistaken for the requested pass completing.
    let statusCall = 0;
    const T1 = "2026-01-02T03:09:05Z";
    const T2 = "2026-01-02T03:15:05Z";
    const T3 = "2026-01-02T03:20:05Z";
    const fetchMock = stubFetch({
      status: () => {
        statusCall += 1;
        if (statusCall === 1) return status({ last_finished_at: T1 });
        if (statusCall === 2) return status({ last_finished_at: T2 });
        if (statusCall === 3) return status({ last_finished_at: T2 });
        return status({ last_finished_at: T3 });
      },
    });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));
    expect(await screen.findByText("requested — picks up within 60s")).toBeInTheDocument();

    // First tick still reports T2 (the same autonomous finish the post-POST
    // read already saw) -- the requested pass has not landed, so the watch
    // must keep waiting rather than declaring completion.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByText("requested — picks up within 60s")).toBeInTheDocument();

    // A later tick reports T3 -- the requested pass actually landing -- and
    // only now should the watch complete.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.queryByText(/picks up within/)).not.toBeInTheDocument();
    expect(callsTo(fetchMock, "/api/status")).toBe(4);
  });

  it("gives up silently once the run's own poll interval plus ten minutes has passed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // 30s poll + 600s == 630s of ticks. A reconcile slower than that is slow,
    // not broken, so the note stays and no error is claimed.
    const fetchMock = stubFetch({ run: async () => json({ status: "requested", poll_seconds: 30 }) });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));
    expect(await screen.findByText("requested — picks up within 30s")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(640000);
    });
    const settled = fetchMock.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(fetchMock.mock.calls.length).toBe(settled);
    expect(screen.getByText("requested — picks up within 30s")).toBeInTheDocument();
    expect(document.querySelector(".page-error")).toBeNull();
  });

  it("stops the watch when a second Diff now supersedes it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = stubFetch();

    render(<Collections />);
    const button = await screen.findByRole("button", { name: "Diff now" });
    fireEvent.click(button);
    expect(await screen.findByText("requested — picks up within 60s")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));
    // One initial status read plus one per click, all settled, so the count
    // below cannot be racing a request still in flight.
    await waitFor(() => expect(callsTo(fetchMock, "/api/status")).toBe(3));

    const before = callsTo(fetchMock, "/api/status");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    // One watch, not two: the superseded interval has to be cleared or the
    // page doubles its status traffic on every press.
    expect(callsTo(fetchMock, "/api/status")).toBe(before + 1);
  });

  it("leaves no timer behind when the page is unmounted mid-watch", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = stubFetch();

    const { unmount } = render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Diff now" }));
    expect(await screen.findByText("requested — picks up within 60s")).toBeInTheDocument();

    unmount();
    const settled = fetchMock.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock.mock.calls.length).toBe(settled);
  });

  it("keeps the empty state when nothing is managed", async () => {
    stubFetch({ collections: { collections: [] } });

    render(<Collections />);

    expect(await screen.findByText("No managed collections yet.")).toBeInTheDocument();
  });

  it("shows the failure rather than an endless spinner", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "database is down" }), {
            status: 500,
            headers: { "Content-Type": "application/json" },
          })
      )
    );

    render(<Collections />);

    expect(await screen.findByText("database is down")).toBeInTheDocument();
  });
});
