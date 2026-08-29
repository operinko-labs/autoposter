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
    jobs_by_state: {
      pending: 0, running: 0, deferred: 0, done: 0, failed: 0, parked: 0, dismissed: 0,
    },
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
        // The server's derived read-time field (api/snapshots.py's
        // `_run_status`), which the reconcile bar's pill now renders instead
        // of `last_status` directly -- see ScheduledRunStatus.tsx. Matches
        // `last_status` by default, the same as a finished run reports both.
        status: "ok",
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

/** The catalog panel mounted on this page fetches for itself. Its own
 * behaviour is covered in CatalogPanel.test.tsx; what these fixtures owe it is
 * a well-formed answer, so a page-level test is not reading an error state the
 * page never shows in life. */
const CATALOG = {
  categories: [
    {
      key: "awards",
      label: "Awards",
      presets: [
        {
          key: "award_cannes",
          name: "Cannes Film Festival",
          titles: ["Cannes Palme d'Or Winners"],
          years_title: 'one per ceremony, named "Cannes <year>"',
          description: "Cannes Film Festival: 1 winners collection.",
          kometa_source: "defaults/award/cannes.yml",
          library_types: ["Movie"],
          readiness: "ready",
          gated_row: null,
          setting: null,
          active: false,
        },
      ],
    },
    { key: "charts", label: "Charts", presets: [] },
  ],
  groups: [
    { key: "awards", title: "Award Collections", section: "010", position: 0 },
    { key: "charts", title: "Chart Collections", section: "020", position: 1 },
  ],
};

const CONFIG = {
  version: "cfg-1",
  collections: { enabled: true, presets: [] },
  overridden_paths: [],
  frozen_paths: {},
  redacted_paths: [],
  keep_sentinel: "***KEEP***",
};

interface StubOptions {
  collections?: Supplied;
  status?: Supplied;
  run?: (path: string, init?: RequestInit) => Promise<Response>;
  poster?: (path: string, init?: RequestInit) => Promise<Response> | Response;
  preview?: (path: string, init?: RequestInit) => Promise<Response> | Response;
}

function stubFetch(options: StubOptions = {}) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path.startsWith("/api/scheduled-runs/") && options.run) return options.run(path, init);
    if (path.startsWith("/api/scheduled-runs/")) return json({ status: "requested", poll_seconds: 60 });
    if (path === "/api/status") return json(supply(options.status, status()));
    // Before the /api/collections/ prefix below, which would otherwise swallow
    // the catalog and hand the picker a list of managed collections.
    if (path === "/api/collections/catalog") return json(CATALOG);
    if (path === "/api/config") return json(CONFIG);
    if (path === "/api/collections/preview") {
      if (options.preview) return options.preview(path, init);
      return json({ definitions: [], actions: [] });
    }
    if (path.startsWith("/api/collections/") && path.endsWith("/poster")) {
      if (options.poster) return options.poster(path, init);
      return json({ status: "installed", applies: "next reconcile" });
    }
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

  it("renders the derived status pill, not the stale last_status, while a reconcile is running", async () => {
    // last_status still says "ok" from the *previous* pass -- the exact
    // stale-outcome shape SCHED-UX killed on the Dashboard. `status` is the
    // server's derived field for the run actually in progress, and the
    // reconcile bar must render that, not last_status, or a multi-minute
    // reconcile would read "ok" for its whole duration here even though the
    // Dashboard's own table correctly shows Running.
    stubFetch({
      status: status({
        last_started_at: "2026-01-02T03:04:00Z",
        last_finished_at: null,
        last_status: "ok",
        status: "running",
      }),
    });

    render(<Collections />);

    const pill = await screen.findByText("Running");
    expect(pill).toHaveClass("pill", "pill-running");
    expect(screen.queryByText("ok")).not.toBeInTheDocument();
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

    // Every fetch fails here, including the catalog panel's own, so the
    // message renders twice. The one this test is about is the page's -- the
    // one outside the panel that reports its own failure for itself.
    const shown = await screen.findAllByText("database is down");
    expect(shown.some((node) => node.closest(".catalog-panel") === null)).toBe(true);
  });

  // --- per-row poster control -----------------------------------------------

  const POSTER_SOURCE = "https://example.com/collection/poster.jpg";

  function posterForm(): HTMLElement {
    const form = document.querySelector<HTMLElement>(".poster-form-row");
    if (form === null) throw new Error("no poster form is open");
    return form;
  }

  it("sets a collection poster by posting the source, and names Diff now", async () => {
    const fetchMock = stubFetch();

    render(<Collections />);

    const row = await rowFor("Marvel Chronological");
    fireEvent.click(row.getByRole("button", { name: "Set poster…" }));
    fireEvent.change(within(posterForm()).getByRole("textbox"), {
      target: { value: POSTER_SOURCE },
    });
    fireEvent.click(within(posterForm()).getByRole("button", { name: "Install" }));

    await waitFor(() => expect(document.querySelector(".poster-note")).not.toBeNull());

    // Posted to this collection's own poster endpoint, carrying exactly the
    // `source` field the endpoint reads.
    const post = fetchMock.mock.calls.find((call) => call[0] === "/api/collections/1/poster");
    expect(post).toBeDefined();
    expect(post![1]?.method).toBe("POST");
    expect(JSON.parse(post![1]!.body as string)).toEqual({ source: POSTER_SOURCE });

    // The endpoint writes the override and stops; the reconciler applies it.
    // The copy names Diff now, which is how the operator triggers that at once.
    expect(document.querySelector(".poster-note")!.textContent).toContain("Diff now");
  });

  it("shows a refused poster inline, without claiming it was installed", async () => {
    stubFetch({
      poster: () => json({ detail: "that source was refused: address is not a public host" }, 422),
    });

    render(<Collections />);

    const row = await rowFor("Marvel Chronological");
    fireEvent.click(row.getByRole("button", { name: "Set poster…" }));
    fireEvent.change(within(posterForm()).getByRole("textbox"), {
      target: { value: POSTER_SOURCE },
    });
    fireEvent.click(within(posterForm()).getByRole("button", { name: "Install" }));

    await waitFor(() =>
      expect(document.querySelector(".poster-error")?.textContent).toBe(
        "that source was refused: address is not a public host",
      ),
    );
    // An error, not a note: showing both would say the poster was refused and
    // installed at once.
    expect(document.querySelector(".poster-note")).toBeNull();
  });

  // --- the Definitions panel --------------------------------------------------
  //
  // No lightweight "list the config definitions" endpoint exists (see the task
  // report), so the panel's only source of definitions is a preview response --
  // "Preview all" (no filters) both seeds the list and reports the delete
  // sweep; each row's own "Preview" button then re-previews just that title.

  const PREVIEW_ALL = {
    definitions: [
      {
        title: "Marvel Chronological",
        library: "Movies",
        adding: 3,
        removing: 1,
        deleting: 0,
        unresolved: 2,
        failed: false,
        skipped: false,
        actions: ["added 3, removed 1 in 'Marvel Chronological'"],
      },
      {
        title: "Best Picture Winners",
        library: "Movies",
        adding: 0,
        removing: 0,
        deleting: 0,
        unresolved: 0,
        failed: true,
        skipped: true,
        actions: [],
      },
    ],
    actions: ["added 3, removed 1 in 'Marvel Chronological'"],
  };

  function definitionsPanel(): HTMLElement {
    const panel = document.querySelector<HTMLElement>(".definitions-panel");
    if (panel === null) throw new Error("no definitions panel");
    return panel;
  }

  it("lists definitions from a Preview all response inside the mobile table-scroll wrapper, failed ones visibly flagged", async () => {
    stubFetch({ preview: () => json(PREVIEW_ALL) });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Preview all" }));

    const panel = within(await waitFor(() => definitionsPanel()));
    const marvelRow = (await panel.findByText("Marvel Chronological")).closest("tr")!;
    expect(within(marvelRow).getByText("Movies")).toBeInTheDocument();
    expect(within(marvelRow).getByText("+3 −1")).toBeInTheDocument();
    expect(within(marvelRow).getByText(/2 unresolved/)).toBeInTheDocument();
    expect(
      within(marvelRow).getByText("added 3, removed 1 in 'Marvel Chronological'"),
    ).toBeInTheDocument();

    // The failed definition is flagged, not just silently present.
    const failedRow = panel.getByText("Best Picture Winners").closest("tr")!;
    expect(within(failedRow).getByText("failed")).toBeInTheDocument();

    // Same mobile idiom as the managed-collections table above.
    expect(panel.getByText("Marvel Chronological")).toHaveClass("cell-title");
    expect(panel.getByText("Marvel Chronological").closest("table")?.parentElement).toHaveClass(
      "table-scroll",
    );
  });

  it("fires a per-definition preview with exactly {library, title} and renders its strings, counts and the no-sweep note", async () => {
    const fetchMock = stubFetch({
      preview: (_path, init) => {
        const body = JSON.parse((init?.body as string) ?? "{}");
        if (body.title !== undefined) {
          return json({
            definitions: [
              {
                title: "Marvel Chronological",
                library: "Movies",
                adding: 5,
                removing: 0,
                deleting: 0,
                unresolved: 0,
                failed: false,
                skipped: false,
                actions: ["added 5 in 'Marvel Chronological'"],
              },
            ],
            actions: ["added 5 in 'Marvel Chronological'"],
          });
        }
        return json(PREVIEW_ALL);
      },
    });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Preview all" }));
    const panel = within(await waitFor(() => definitionsPanel()));
    const marvelRow = (await panel.findByText("Marvel Chronological")).closest("tr")!;

    // The mutation proof this test carries: the per-definition button must send
    // exactly {library, title}. A body that dropped `title` would fall through
    // to the "Preview all" branch above and this assertion would see the old
    // (adding: 3) row instead of the new (adding: 5) one -- red.
    fireEvent.click(within(marvelRow).getByRole("button", { name: "Preview" }));

    await waitFor(() => expect(within(marvelRow).getByText("+5 −0")).toBeInTheDocument());
    expect(
      within(marvelRow).getByText("added 5 in 'Marvel Chronological'"),
    ).toBeInTheDocument();

    const previewCalls = fetchMock.mock.calls.filter(
      ([path]) => path === "/api/collections/preview",
    );
    expect(previewCalls).toHaveLength(2);
    expect(JSON.parse(previewCalls[1]![1]!.body as string)).toEqual({
      library: "Movies",
      title: "Marvel Chronological",
    });

    // The UI must not imply a per-definition preview ran the delete sweep.
    expect(screen.getByText(/skips the delete sweep/)).toBeInTheDocument();
  });

  it("shows would-delete entries from Preview all", async () => {
    stubFetch({
      preview: () =>
        json({
          definitions: [
            ...PREVIEW_ALL.definitions,
            {
              title: "Old One-Off",
              library: "Movies",
              adding: 0,
              removing: 0,
              deleting: 1,
              unresolved: 0,
              failed: false,
              skipped: true,
              actions: ["would delete 'Old One-Off': no definition builds it"],
            },
          ],
          actions: [],
        }),
    });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Preview all" }));

    const panel = within(await waitFor(() => definitionsPanel()));
    const sweptRow = (await panel.findByText("Old One-Off")).closest("tr")!;
    expect(within(sweptRow).getByText(/deleting 1/)).toBeInTheDocument();
    expect(
      within(sweptRow).getByText("would delete 'Old One-Off': no definition builds it"),
    ).toBeInTheDocument();
  });

  it("shows the disabled-collections 503 as a clear state, not a crash", async () => {
    stubFetch({
      preview: () =>
        json({ detail: "collections are disabled in the config for this instance" }, 503),
    });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Preview all" }));

    expect(
      await screen.findByText("collections are disabled in the config for this instance"),
    ).toBeInTheDocument();
    // The panel itself is still there, with a working control -- not a crash.
    expect(screen.getByRole("button", { name: "Preview all" })).toBeEnabled();
  });

  it("renders a per-library preview failure entry as a clear state, not a crash", async () => {
    stubFetch({
      preview: () =>
        json({
          definitions: [
            {
              title: "(library)",
              library: "Shows",
              adding: 0,
              removing: 0,
              deleting: 0,
              unresolved: 0,
              failed: true,
              skipped: true,
              actions: ["Shows: could not be previewed (RuntimeError)"],
            },
          ],
          actions: ["Shows: could not be previewed (RuntimeError)"],
        }),
    });

    render(<Collections />);
    fireEvent.click(await screen.findByRole("button", { name: "Preview all" }));

    const panel = within(await waitFor(() => definitionsPanel()));
    const row = (await panel.findByText("(library)")).closest("tr")!;
    expect(within(row).getByText("failed")).toBeInTheDocument();
    expect(
      within(row).getByText("Shows: could not be previewed (RuntimeError)"),
    ).toBeInTheDocument();
    // The rest of the page survived it.
    expect(screen.getByRole("heading", { name: "Collections" })).toBeInTheDocument();
  });

  it("mounts the catalog picker below the definitions it adds to", async () => {
    stubFetch();

    render(<Collections />);

    // The panel's own behaviour is CatalogPanel.test.tsx's; what belongs here
    // is that the page mounts it and that it reaches its own endpoint rather
    // than being handed the managed-collections list by the fixture's
    // catch-all.
    const strip = await screen.findByRole("tablist", { name: "Catalog categories" });
    expect(within(strip).getByRole("tab", { name: "Awards" })).toBeInTheDocument();
    expect(
      await screen.findByRole("checkbox", { name: /Cannes Film Festival/ }),
    ).toBeInTheDocument();
  });

  it("mounts the groups panel below the catalog", async () => {
    stubFetch();

    render(<Collections />);

    expect(
      await screen.findByRole("list", { name: "Collection group order" }),
    ).toBeInTheDocument();
  });
});
