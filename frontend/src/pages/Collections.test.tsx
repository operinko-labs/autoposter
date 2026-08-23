import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

interface StubOptions {
  collections?: unknown;
  status?: unknown;
  run?: (path: string, init?: RequestInit) => Promise<Response>;
}

function stubFetch(options: StubOptions = {}) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.startsWith("/api/scheduled-runs/") && options.run) return options.run(path, init);
    if (path.startsWith("/api/scheduled-runs/")) return json({ status: "requested", poll_seconds: 60 });
    if (path === "/api/status") return json(options.status ?? status());
    return json(options.collections ?? COLLECTIONS);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
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
