import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Failures } from "./Failures";

const PARKED = {
  id: 41,
  kind: "render_poster",
  attempts: 5,
  reason: "TMDB returned 503",
  updated_at: "2026-01-02T03:04:05Z",
  title: "Andor",
  item_kind: "episode",
  season_number: 2,
  episode_number: 5,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  setToken(null);
});

describe("Failures", () => {
  it("lists parked jobs with the reason they stopped", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ jobs: [PARKED] })));

    render(<Failures />);

    expect(await screen.findByText("TMDB returned 503")).toBeInTheDocument();
    expect(screen.getByText(/render_poster/)).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("1 parked job")).toBeInTheDocument();
  });

  it("names the item, with its season and episode numbers", async () => {
    // The report this fix answers: an operator on Failures saw a job id and
    // a bare reason class, with no way to tell which show, episode or movie
    // it was about.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ jobs: [PARKED] })));

    render(<Failures />);

    expect(await screen.findByText("Andor")).toBeInTheDocument();
    expect(screen.getByText("S02E05")).toBeInTheDocument();
  });

  it("shows a dash for a job whose payload named nothing", async () => {
    const NAMELESS = {
      ...PARKED,
      id: 42,
      title: null,
      item_kind: null,
      season_number: null,
      episode_number: null,
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ jobs: [NAMELESS] })));

    render(<Failures />);

    expect(await screen.findByText("render_poster")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("retries a job and drops the row once the server confirms", async () => {
    // The list is re-read after the action rather than mutated locally, so
    // the second GET is what removes the row. Returning the job again here
    // would (correctly) leave it on screen.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ jobs: [PARKED] }))
      .mockResolvedValueOnce(json({ status: "queued" }))
      .mockResolvedValueOnce(json({ jobs: [] }));
    vi.stubGlobal("fetch", fetchMock);

    render(<Failures />);
    const retry = await screen.findByRole("button", { name: "Retry" });

    fireEvent.click(retry);

    await waitFor(() => expect(screen.getByText("Nothing parked.")).toBeInTheDocument());

    expect(fetchMock.mock.calls[1][0]).toBe("/api/jobs/41/retry");
    expect(fetchMock.mock.calls[1][1].method).toBe("POST");
    // Re-read from the server, not removed optimistically.
    expect(fetchMock.mock.calls[2][0]).toBe("/api/jobs/parked");
    expect(screen.queryByText("TMDB returned 503")).not.toBeInTheDocument();
  });

  it("dismisses a job through the dismiss endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ jobs: [PARKED] }))
      .mockResolvedValueOnce(json({ status: "dismissed" }))
      .mockResolvedValueOnce(json({ jobs: [] }));
    vi.stubGlobal("fetch", fetchMock);

    render(<Failures />);
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/jobs/41/dismiss");
  });

  it("wraps the reason, contains the table, and keeps the actions grouped", async () => {
    // The 500px sweep: an unwrapped reason widened the table past the content
    // column, and Retry/Dismiss went off screen with it. jsdom computes no
    // layout, so what is assertable here is that the three hooks the CSS
    // hangs off are on the right nodes -- the widths themselves were checked
    // in a browser.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ jobs: [PARKED] })));

    render(<Failures />);
    const reason = await screen.findByText("TMDB returned 503");

    expect(reason).toHaveClass("cell-wrap");
    expect(reason.closest("table")?.parentElement).toHaveClass("table-scroll");

    const actions = screen.getByRole("button", { name: "Retry" }).parentElement;
    expect(actions).toHaveClass("row-actions");
    expect(actions).toContainElement(screen.getByRole("button", { name: "Dismiss" }));
  });

  it("keeps the row and reports the error when the action fails", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ jobs: [PARKED] }))
      .mockResolvedValueOnce(json({ detail: "job is no longer parked" }, 409));
    vi.stubGlobal("fetch", fetchMock);

    render(<Failures />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));

    expect(await screen.findByText("job is no longer parked")).toBeInTheDocument();
    expect(screen.getByText("TMDB returned 503")).toBeInTheDocument();
  });
});
