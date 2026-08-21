import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Failures } from "./Failures";

const PARKED = {
  id: 41,
  kind: "render_poster",
  payload: { item_id: 9 },
  attempts: 5,
  reason: "TMDB returned 503",
  updated_at: "2026-01-02T03:04:05Z",
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
