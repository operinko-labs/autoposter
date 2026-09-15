import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { DRIFT_NOTICE, DriftNotice } from "./DriftNotice";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Every GET answers with the drift report and every POST with `post`, which
 * is what an import gets. Routed by method rather than by call order, for the
 * reason `Settings.test.tsx` gives: a test that answered "first call, second
 * call" would pass against a component that sent them in either order. */
function stubDrift(body: unknown, post: Response = json({ version_after: "abc" })) {
  const fetchMock = vi.fn((_input: string, init?: RequestInit) =>
    Promise.resolve((init?.method ?? "GET") === "GET" ? json(body) : post.clone()),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const DIFFERING = {
  file_present: true,
  differs: true,
  paths: ["workers", "scheduler.poll_seconds"],
  path: "/config/autoposter.yaml",
  document: { workers: 9 },
};

/** The report is fetched on mount, so a render that is not awaited leaves a
 * state update outside act(). */
async function mount(
  props: { revision?: string | null; onChanged?: () => void } = {},
) {
  await act(async () => {
    render(
      <DriftNotice
        revision={props.revision ?? null}
        onChanged={props.onChanged ?? (() => {})}
      />,
    );
  });
}

async function press(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

const CONFIRM_LABEL = "Allow the import to remove settings I have saved";

/** The body of the one import the notice sent. */
function importBody(fetchMock: ReturnType<typeof stubDrift>): unknown {
  const calls = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(calls).toHaveLength(1);
  expect(calls[0][0]).toBe("/api/config/overrides/import");
  return JSON.parse(String(calls[0][1]?.body));
}

beforeEach(() => {
  setToken(null);
  // jsdom implements neither, and the export hands the envelope over as a
  // blob for the same reason the backup panel does.
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:stub"),
    revokeObjectURL: vi.fn(),
  });
});

describe("DriftNotice", () => {
  it("renders nothing when the file agrees with the store", async () => {
    stubDrift({
      file_present: true,
      differs: false,
      paths: [],
      path: "/config/autoposter.yaml",
      document: null,
    });
    let container: HTMLElement | null = null;
    await act(async () => {
      container = render(<DriftNotice onChanged={() => {}} />).container;
    });

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there is no file at all", async () => {
    // Removing the mounted file once the store is seeded is the intended end
    // state; a permanent notice for having done it would be worse than none.
    stubDrift({
      file_present: false,
      differs: false,
      paths: [],
      path: null,
      document: null,
    });
    let container: HTMLElement | null = null;
    await act(async () => {
      container = render(<DriftNotice onChanged={() => {}} />).container;
    });

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the report cannot be read", async () => {
    // A footnote about a file is not worth a page error on the page whose job
    // is the configuration.
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Failed to fetch")));
    let container: HTMLElement | null = null;
    await act(async () => {
      container = render(<DriftNotice onChanged={() => {}} />).container;
    });

    expect(container).toBeEmptyDOMElement();
  });

  it("names the notice and the differing paths, and offers Import and Export", async () => {
    stubDrift(DIFFERING);

    await mount();

    expect(screen.getByText(DRIFT_NOTICE)).toBeInTheDocument();
    expect(screen.getByText(/workers, scheduler.poll_seconds/)).toBeInTheDocument();
    expect(screen.getByText("/config/autoposter.yaml")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import the file" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export the store" })).toBeInTheDocument();
  });

  it("says why there are no paths when the two cannot be walked side by side", async () => {
    // A file the schema refuses is reported as a difference with no paths:
    // there is no second document to name them from.
    stubDrift({
      file_present: true,
      differs: true,
      paths: [],
      path: "/config/autoposter.yaml",
      document: { workers: "lots" },
    });

    await mount();

    expect(screen.getByText(DRIFT_NOTICE)).toBeInTheDocument();
    expect(screen.queryByText(/Differing settings:/)).toBeNull();
    expect(
      screen.getByText(/no longer describes a configuration this service can build/),
    ).toBeInTheDocument();
  });

  it("imports the document the report was made from, with the confirm unticked", async () => {
    // The drop cap is the server's, and the page never ticks it on the
    // operator's behalf: importing a file the store has outgrown drops every
    // setting the file does not mention, which is what the cap is for.
    const fetchMock = stubDrift(DIFFERING);
    await mount();

    await press("Import the file");

    expect(importBody(fetchMock)).toEqual({
      autoposter_overrides: 1,
      document: { workers: 9 },
      confirm: false,
    });
  });

  it("sends the tick and the page's revision when both are there", async () => {
    const fetchMock = stubDrift(DIFFERING);
    await mount({ revision: "rev-7" });

    fireEvent.click(screen.getByLabelText(CONFIRM_LABEL));
    await press("Import the file");

    expect(importBody(fetchMock)).toEqual({
      autoposter_overrides: 1,
      document: { workers: 9 },
      confirm: true,
      expected_revision: "rev-7",
    });
  });

  it("re-reads the configuration and the report after an import", async () => {
    const seen: string[] = [];
    stubDrift(DIFFERING);
    await mount({
      onChanged: () => {
        seen.push("config");
      },
    });

    await press("Import the file");

    // The notice describes a comparison the import has just changed, so it
    // asks again rather than standing there describing the old one.
    expect(seen).toEqual(["config"]);
  });

  it("renders the server's refusal rather than a sentence of its own", async () => {
    const refusal =
      "this would drop 40 stored overrides (artwork.use_logo, badges.enabled); " +
      "send confirm: true to do it deliberately";
    stubDrift(DIFFERING, json({ detail: refusal }, 422));
    await mount();

    await press("Import the file");

    expect(screen.getByText(refusal)).toBeInTheDocument();
    // The tick is back to unticked: a confirm made for this press does not
    // carry over to the next one, win or lose.
    expect(screen.getByLabelText(CONFIRM_LABEL)).not.toBeChecked();
  });

  it("exports through the endpoint rather than a bare link to it", async () => {
    // The export answers only to a bearer header, so a plain <a href> to it
    // would download nothing. The bytes are fetched and handed over as a blob.
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) =>
        Promise.resolve(
          input === "/api/config/overrides/export"
            ? json({
                autoposter_overrides: 1,
                exported_at: "2026-09-14T10:11:12.5Z",
                document: { workers: 9 },
              })
            : json(DIFFERING),
        ),
      ),
    );
    await mount();

    await press("Export the store");

    const link = screen.getByTestId("drift-export-link");
    expect(link.getAttribute("download")).toBe(
      "autoposter-overrides-2026-09-14T10-11-12-5Z.json",
    );
  });
});
