import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Logs } from "./Logs";

const INFO_LINE = {
  ts: "2026-08-23T10:11:12+00:00",
  level: "INFO",
  logger: "autoposter.queue.worker",
  message: "job 12 done",
};

const ERROR_LINE = {
  ts: "2026-08-23T10:11:13+00:00",
  level: "ERROR",
  logger: "autoposter.render.pipeline",
  message: "magick failed",
};

/** A streamed NDJSON body. The stream never closes (a closed stream makes the
 * page schedule a reconnect, which would leak a timer into the next test), so
 * each test aborts it by unmounting. */
function ndjsonResponse(...values: unknown[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const value of values) {
        controller.enqueue(encoder.encode(JSON.stringify(value) + "\n"));
      }
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "application/x-ndjson" },
  });
}

beforeEach(() => {
  setToken("a-session-token");
});

describe("Logs", () => {
  it("renders lines from the stream and reports the live state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(ndjsonResponse(INFO_LINE, ERROR_LINE)),
    );

    const { unmount } = render(<Logs />);

    expect(await screen.findByText("job 12 done")).toBeInTheDocument();
    expect(screen.getByText("magick failed")).toBeInTheDocument();
    expect(screen.getByText("live")).toBeInTheDocument();
    unmount();
  });

  it("ignores heartbeat lines rather than rendering them", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(ndjsonResponse({ heartbeat: true }, INFO_LINE)),
    );

    const { unmount } = render(<Logs />);

    expect(await screen.findByText("job 12 done")).toBeInTheDocument();
    expect(screen.getByText("1 of 1 line")).toBeInTheDocument();
    unmount();
  });

  it("filters by level without discarding the hidden lines", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(ndjsonResponse(INFO_LINE, ERROR_LINE)),
    );

    const { unmount } = render(<Logs />);
    await screen.findByText("job 12 done");

    fireEvent.change(screen.getByLabelText(/Level/), { target: { value: "ERROR" } });

    expect(screen.queryByText("job 12 done")).not.toBeInTheDocument();
    expect(screen.getByText("magick failed")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 lines")).toBeInTheDocument();
    unmount();
  });

  it("shows the error when the stream cannot be opened", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "boom" }), { status: 500 }),
      ),
    );

    const { unmount } = render(<Logs />);

    expect(await screen.findByText("boom")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("reconnecting…")).toBeInTheDocument());
    unmount();
  });
});
