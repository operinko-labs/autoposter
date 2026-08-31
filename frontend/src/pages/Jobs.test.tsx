import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Jobs } from "./Jobs";

const PENDING = {
  id: 7,
  kind: "process_item",
  state: "pending",
  attempts: 1,
  max_attempts: 5,
  waiting_for_plex: false,
  title: "Dune",
  item_kind: "movie",
  season_number: null,
  episode_number: null,
  run_in_seconds: 150,
  last_error: "RuntimeError",
  waiting_reason: null,
  created_at: "2026-01-02T03:04:05Z",
};

const RUNNING = {
  ...PENDING,
  id: 8,
  state: "running",
  attempts: 2,
  title: "Andor",
  item_kind: "episode",
  season_number: 2,
  episode_number: 5,
  run_in_seconds: -3,
  last_error: null,
};

const DEFERRED = {
  ...PENDING,
  id: 9,
  state: "deferred",
  attempts: 0,
  max_attempts: null,
  waiting_for_plex: true,
  title: "Sinners",
  run_in_seconds: 21600,
  last_error: null,
  waiting_reason: "no Plex item for movie 'Sinners' (tmdb=1, tvdb=None)",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function list(...jobs: unknown[]): Response {
  return json({ jobs, total: jobs.length });
}

beforeEach(() => {
  setToken(null);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Jobs", () => {
  it("lists what the queue is pending and running", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(list(RUNNING, PENDING)));

    render(<Jobs />);

    expect(await screen.findByText("Dune")).toBeInTheDocument();
    expect(screen.getByText("Andor")).toBeInTheDocument();
    expect(screen.getAllByText("process_item")).toHaveLength(2);
    expect(screen.getByText("pending")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("shows the empty state when nothing is queued", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(list()));

    render(<Jobs />);

    expect(await screen.findByText("No queued, running or waiting jobs.")).toBeInTheDocument();
  });

  it("names the episode with its season and episode numbers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(list(RUNNING)));

    render(<Jobs />);

    expect(await screen.findByText("S02E05")).toBeInTheDocument();
  });

  it("shows a deferred job as waiting against no budget at all", async () => {
    // The whole point of the state: a deferred job is not counting down to
    // anything. "0/5" would read as a job four failures from parked; "0/∞"
    // plus the badge reads as what it is.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(list(DEFERRED, PENDING)));

    render(<Jobs />);

    expect(await screen.findByText("0/∞")).toBeInTheDocument();
    expect(screen.getByText("1/5")).toBeInTheDocument();
    expect(screen.getByText("waiting for Plex")).toBeInTheDocument();
    expect(screen.getByText("deferred")).toBeInTheDocument();
    // Six hours out, and said as such rather than as a stalled countdown.
    expect(screen.getByText("in 6h")).toBeInTheDocument();
  });

  it("shows what a deferred job waits for as a reason, not as an error", async () => {
    // The row has no error -- nothing failed. Rendering its reason in the
    // error column unmarked is the "parked movie" reading this whole state
    // exists to stop.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(list(DEFERRED)));

    render(<Jobs />);

    const reason = await screen.findByText(
      "no Plex item for movie 'Sinners' (tmdb=1, tvdb=None)",
    );
    expect(reason).toHaveClass("jobs-reason");
  });

  it("counts down to the next attempt, and says now for a job already due", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(list(RUNNING, PENDING)));

    render(<Jobs />);

    expect(await screen.findByText("in 3m")).toBeInTheDocument();
    expect(screen.getByText("now")).toBeInTheDocument();
  });

  it("cancels a pending job and re-reads the list", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(list(PENDING))
      .mockResolvedValueOnce(json({ id: 7, state: "dismissed", cancelled: true }))
      .mockResolvedValueOnce(list());
    vi.stubGlobal("fetch", fetchMock);

    render(<Jobs />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() =>
      expect(screen.getByText("No queued, running or waiting jobs.")).toBeInTheDocument(),
    );
    expect(fetchMock.mock.calls[1][0]).toBe("/api/jobs/7/cancel");
    expect(fetchMock.mock.calls[1][1].method).toBe("POST");
    // Re-read from the server rather than dropped locally: the queue is the
    // authority on what state a job is now in.
    expect(fetchMock.mock.calls[2][0]).toBe("/api/jobs");
  });

  it("reports inline that a running job finishes its attempt first", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(list(RUNNING))
      .mockResolvedValueOnce(
        json({
          id: 8,
          state: "running",
          cancel_requested: true,
          detail: "the job will finish its current attempt and will not be retried",
        }),
      )
      .mockResolvedValueOnce(list(RUNNING));
    vi.stubGlobal("fetch", fetchMock);

    render(<Jobs />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(
      await screen.findByText(
        "the job will finish its current attempt and will not be retried",
      ),
    ).toBeInTheDocument();
    // Still listed: it is running, and the worker has not finished with it.
    expect(screen.getByText("Andor")).toBeInTheDocument();
  });

  it("shows a persisted cancel as a badge and disables the button", async () => {
    // Not the client-only note from a cancel click -- this comes back from
    // the list itself, which is what a reload has to go on.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(list({ ...RUNNING, cancel_requested: true })),
    );

    render(<Jobs />);

    expect(await screen.findByText("cancelling…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("renders a refused cancel as that row's error", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(list(PENDING))
      .mockResolvedValueOnce(json({ detail: "job is already parked" }, 409))
      .mockResolvedValueOnce(list(PENDING));
    vi.stubGlobal("fetch", fetchMock);

    render(<Jobs />);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(await screen.findByText("job is already parked")).toBeInTheDocument();
  });

  it("re-reads the list every five seconds while mounted, and stops on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = vi.fn().mockResolvedValue(list(PENDING));
    vi.stubGlobal("fetch", fetchMock);

    const view = render(<Jobs />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);

    view.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("contains the table, wraps the error and groups the actions", async () => {
    // The mobile conventions, as on Failures: an unwrapped error string widens
    // the table past the content column and takes the row's own button off
    // screen with it. jsdom computes no layout, so what is assertable is that
    // the hooks the CSS hangs off are on the right nodes.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(list(PENDING)));

    render(<Jobs />);
    const error = await screen.findByText("RuntimeError");

    expect(error).toHaveClass("cell-wrap");
    expect(error.closest("table")?.parentElement).toHaveClass("table-scroll");
    expect(screen.getByRole("button", { name: "Cancel" }).parentElement).toHaveClass(
      "row-actions",
    );
    expect(screen.getByText(/2026/)).toHaveClass("cell-time");
  });
});
