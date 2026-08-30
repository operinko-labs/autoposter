/** The facts-backfill button: one POST per press, the server re-read as the
 * authority afterwards (the GroupsPanel posture -- never patch state from
 * memory of what was clicked). "Parked" and "complete" are statuses, not
 * errors: parked keeps the button live, complete retires it. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { FactsBackfillPanel } from "./FactsBackfillPanel";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface StubOptions {
  gets?: unknown[];
  post?: () => Response;
}

/** GET answers are consumed in order (mount, then one re-read per POST); the
 * last one repeats. */
function stubFetch(options: StubOptions = {}) {
  const gets = [...(options.gets ?? [{ status: "not_started", done: 0, total: 4 }])];
  const posts: RequestInit[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/facts/backfill" && init?.method === "POST") {
      posts.push(init);
      if (options.post) return options.post();
      return json({
        status: "enqueued", enqueued: 2, done: 2, total: 4,
        detail: "enqueued 2 item(s); 2 of 4 walked so far",
      });
    }
    if (path === "/api/facts/backfill") {
      return json(gets.length > 1 ? gets.shift() : gets[0]);
    }
    throw new Error(`unexpected fetch: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, posts };
}

/** How many times the standing progress was read. The POST answers with
 * counts of its own, so "the panel re-read" is only demonstrable by counting
 * the GETs -- matching the numbers on screen cannot tell a re-read from a
 * patch of the POST body. */
function getCalls(fetchMock: ReturnType<typeof stubFetch>["fetchMock"]) {
  return fetchMock.mock.calls.filter(([, init]) => init?.method !== "POST").length;
}

async function renderPanel(options: StubOptions = {}) {
  const stub = stubFetch(options);
  render(<FactsBackfillPanel />);
  await screen.findByRole("button", { name: "Enqueue next batch" });
  return stub;
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setToken("test-token");
});

describe("FactsBackfillPanel", () => {
  it("renders the standing progress from the GET", async () => {
    await renderPanel({ gets: [{ status: "in_progress", done: 2, total: 4 }] });
    expect(screen.getByText(/2 of 4/)).toBeInTheDocument();
  });

  it("the button POSTs once and re-reads the standing progress", async () => {
    const { fetchMock, posts } = await renderPanel({
      gets: [
        { status: "not_started", done: 0, total: 4 },
        { status: "in_progress", done: 2, total: 4 },
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "Enqueue next batch" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    await waitFor(() => expect(getCalls(fetchMock)).toBe(2));
    // The progress line specifically: the server's detail below it carries
    // "2 of 4" too, so a bare /2 of 4/ matches both paragraphs.
    await screen.findByText(/2 of 4 movies and shows walked/);
    expect(screen.getByText(/enqueued 2 item\(s\)/i)).toBeInTheDocument();
  });

  it("a parked trigger shows the server's detail and keeps the button live", async () => {
    await renderPanel({
      post: () =>
        json({
          status: "parked", enqueued: 0, done: 0, total: 4,
          detail: "TMDb is inside its 429 backoff window; nothing was enqueued.",
        }),
    });
    fireEvent.click(screen.getByRole("button", { name: "Enqueue next batch" }));
    await screen.findByText(/backoff window/);
    expect(screen.getByRole("button", { name: "Enqueue next batch" })).toBeEnabled();
  });

  it("a complete walk disables the button", async () => {
    await renderPanel({ gets: [{ status: "complete", done: 4, total: 4 }] });
    expect(screen.getByRole("button", { name: "Enqueue next batch" })).toBeDisabled();
  });

  it("a failed POST shows the error verbatim", async () => {
    await renderPanel({
      post: () => json({ detail: "boom from the server" }, 500),
    });
    fireEvent.click(screen.getByRole("button", { name: "Enqueue next batch" }));
    await screen.findByText(/boom from the server/);
  });

  it("a failed GET is the panel's own error, not a crash", async () => {
    const fetchMock = vi.fn(async () => json({ detail: "backfill status broke" }, 500));
    vi.stubGlobal("fetch", fetchMock);
    render(<FactsBackfillPanel />);
    await screen.findByText(/backfill status broke/);
  });
});
