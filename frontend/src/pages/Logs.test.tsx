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
 * each test aborts it by unmounting. It errors its own controller when the
 * fetch's AbortSignal fires, matching what a real fetch body does -- without
 * that, `apiFetchNdjson`'s pending `reader.read()` never settles and unmount
 * leaves it dangling. */
function ndjsonResponse(signal: AbortSignal, ...values: unknown[]): Response {
  let controller: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
      const encoder = new TextEncoder();
      for (const value of values) {
        controller.enqueue(encoder.encode(JSON.stringify(value) + "\n"));
      }
    },
  });
  signal.addEventListener("abort", () => {
    controller.error(new DOMException("aborted", "AbortError"));
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
      vi.fn((_url: string, init: RequestInit) =>
        Promise.resolve(ndjsonResponse(init.signal as AbortSignal, INFO_LINE, ERROR_LINE)),
      ),
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
      vi.fn((_url: string, init: RequestInit) =>
        Promise.resolve(
          ndjsonResponse(init.signal as AbortSignal, { heartbeat: true }, INFO_LINE),
        ),
      ),
    );

    const { unmount } = render(<Logs />);

    expect(await screen.findByText("job 12 done")).toBeInTheDocument();
    expect(screen.getByText("1 of 1 line")).toBeInTheDocument();
    // A heartbeat that leaked past isLogLine would otherwise crash the
    // render before this line runs (line.ts is undefined) rather than fail
    // it -- assert directly that no trace of the heartbeat reached the DOM.
    expect(document.body.textContent ?? "").not.toContain("heartbeat");
    unmount();
  });

  it("filters by level without discarding the hidden lines", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit) =>
        Promise.resolve(ndjsonResponse(init.signal as AbortSignal, INFO_LINE, ERROR_LINE)),
      ),
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
