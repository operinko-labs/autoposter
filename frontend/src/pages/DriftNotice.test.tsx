/** The notice, and the two refusal shapes the import route really sends.
 *
 * The fixtures below are the wire shapes, not sentences: the drop cap answers
 * `detail: [{path, message}]` and the stale-revision check answers
 * `detail: {message, …}`. A test that stubbed a bare string would pass against
 * a component that shows `request failed with 422` where the sentence telling
 * the operator to tick the box belongs.
 */
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
function stubDrift(
  body: unknown,
  post: Response = json({ version_before: "abc123", version_after: "def456" }),
) {
  const fetchMock = vi.fn((_input: string, init?: RequestInit) =>
    Promise.resolve((init?.method ?? "GET") === "GET" ? json(body) : post.clone()),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The export envelope, for the two tests that press Export. */
function stubExport() {
  const fetchMock = vi.fn((input: string) =>
    Promise.resolve(
      input === "/api/config/overrides/export"
        ? json({
            autoposter_overrides: 1,
            exported_at: "2026-09-14T10:11:12.5Z",
            document: { workers: 9 },
          })
        : json(DIFFERING),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const DIFFERING = {
  file_present: true,
  differs: true,
  paths: ["workers", "scheduler.poll_seconds"],
  path: "/config/autoposter.yaml",
  file_revision: "file-rev-1",
};

const AGREEING = {
  file_present: true,
  differs: false,
  paths: [],
  path: "/config/autoposter.yaml",
  file_revision: "file-rev-1",
};

/** The report is fetched on mount, so a render that is not awaited leaves a
 * state update outside act(). */
async function mount(
  props: {
    revision?: string | null;
    pendingEdits?: boolean;
    onChanged?: () => void;
  } = {},
) {
  await act(async () => {
    render(
      <DriftNotice
        revision={props.revision ?? null}
        pendingEdits={props.pendingEdits ?? false}
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
  expect(calls[0][0]).toBe("/api/config/drift/import");
  return JSON.parse(String(calls[0][1]?.body));
}

function getsTo(fetchMock: ReturnType<typeof stubDrift>, url: string): number {
  return fetchMock.mock.calls.filter(
    ([input, init]) => input === url && (init?.method ?? "GET") === "GET",
  ).length;
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
    stubDrift(AGREEING);
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
      file_revision: null,
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
    stubDrift({ ...DIFFERING, paths: [] });

    await mount();

    expect(screen.getByText(DRIFT_NOTICE)).toBeInTheDocument();
    expect(screen.queryByText(/Differing settings:/)).toBeNull();
    expect(
      screen.getByText(/no longer describes a configuration this service can build/),
    ).toBeInTheDocument();
  });

  it("asks the server to read the file, and names no document", async () => {
    // The whole point of the route: the file's contents never cross the wire,
    // so a notification token cannot leave with a background read, and there
    // is no arm where a client names the document that gets stored.
    const fetchMock = stubDrift(DIFFERING);
    await mount({ revision: "rev-7" });

    await press("Import the file");

    expect(importBody(fetchMock)).toEqual({
      confirm: false,
      expected_revision: "rev-7",
      expected_file_revision: "file-rev-1",
    });
  });

  it("sends the tick the operator made and nothing it made up", async () => {
    // The drop cap is the server's, and the page never ticks it on the
    // operator's behalf: importing a file the store has outgrown drops every
    // setting the file does not mention, which is what the cap is for.
    const fetchMock = stubDrift(DIFFERING);
    await mount();

    fireEvent.click(screen.getByLabelText(CONFIRM_LABEL));
    await press("Import the file");

    expect(importBody(fetchMock)).toEqual({
      confirm: true,
      expected_file_revision: "file-rev-1",
    });
  });

  it("re-reads the configuration and the report after an import, and says so", async () => {
    const seen: string[] = [];
    const fetchMock = stubDrift(AGREEING);
    // The report differs on mount and agrees once the import has landed, which
    // is what the notice going away depends on.
    fetchMock.mockImplementationOnce(() => Promise.resolve(json(DIFFERING)));
    await mount({
      onChanged: () => {
        seen.push("config");
      },
    });

    await press("Import the file");

    expect(seen).toEqual(["config"]);
    // The notice describes a comparison the import has just changed, so it
    // asks again rather than standing there describing the old one.
    expect(getsTo(fetchMock, "/api/config/drift")).toBe(2);
    // And the acknowledgement outlives the notice it replaces -- otherwise a
    // successful import shows nothing at all.
    expect(
      screen.getByText("Imported the file. Config abc123 → def456."),
    ).toBeInTheDocument();
    expect(screen.queryByText(DRIFT_NOTICE)).toBeNull();
  });

  it("renders the drop cap's own sentence, out of the list the server sends", async () => {
    const refusal =
      "this would drop 40 stored overrides (artwork.use_logo, badges.enabled); " +
      "send confirm: true to do it deliberately";
    stubDrift(
      DIFFERING,
      json({ detail: [{ path: "document", message: refusal }] }, 422),
    );
    await mount();

    await press("Import the file");

    expect(screen.getByText(refusal)).toBeInTheDocument();
    // The tick is back to unticked: a confirm made for this press does not
    // carry over to the next one, win or lose.
    expect(screen.getByLabelText(CONFIRM_LABEL)).not.toBeChecked();
  });

  it("renders the stale-revision refusal, out of the object the server sends", async () => {
    const message =
      "these settings changed somewhere else while this page was open; " +
      "nothing was saved";
    stubDrift(
      DIFFERING,
      json(
        { detail: { message, current_revision: "rev-9", changed_paths: ["workers"] } },
        409,
      ),
    );
    await mount({ revision: "rev-7" });

    await press("Import the file");

    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it("renders a plain-string refusal as it stands", async () => {
    const message =
      "the configuration file changed while this page was open, so nothing " +
      "was imported; open the page again to see what it says now";
    stubDrift(DIFFERING, json({ detail: message }, 409));
    await mount();

    await press("Import the file");

    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it("refuses the import while the page is holding an unsaved edit", async () => {
    // The import replaces the whole stored configuration and this notice then
    // re-seeds the page from it, so a press would throw the typing away with
    // no message at all. The same rule, and the same sentence, as the restart
    // button's -- carrying the edit across an import is not on offer.
    const fetchMock = stubDrift(DIFFERING);
    await mount({ pendingEdits: true });

    expect(screen.getByRole("button", { name: "Import the file" })).toBeDisabled();
    expect(
      screen.getByText(/Save or discard the changes below first/),
    ).toBeInTheDocument();
    // The export writes nothing, so it stays available: it is how an operator
    // keeps a copy of what is stored before deciding.
    expect(screen.getByRole("button", { name: "Export the store" })).toBeEnabled();
    expect(
      fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(0);
  });

  it("re-reads after a stale refusal, so the next press is composed against what the server holds", async () => {
    // Both 409 arms leave this component holding the value that caused them --
    // the page's revision, or the file's -- so without a re-read every press
    // for the life of the page repeats the same refusal.
    const reports = [
      json(DIFFERING),
      json({ ...DIFFERING, file_revision: "file-rev-2" }),
    ];
    const posts = [
      json(
        {
          detail:
            "the configuration file changed while this page was open, so " +
            "nothing was imported",
        },
        409,
      ),
      json({ version_before: "abc123", version_after: "def456" }),
    ];
    const fetchMock = vi.fn((_input: string, init?: RequestInit) =>
      Promise.resolve(
        (init?.method ?? "GET") === "GET"
          ? (reports.shift() ?? json(DIFFERING))
          : (posts.shift() ?? json({})),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onChanged = vi.fn();
    await mount({ onChanged });

    await press("Import the file");
    expect(
      screen.getByText(/the configuration file changed while this page was open/),
    ).toBeInTheDocument();
    // The configuration and the report, both re-read: the refusal named one of
    // them as stale and the page cannot tell which.
    expect(onChanged).toHaveBeenCalled();
    expect(getsTo(fetchMock, "/api/config/drift")).toBe(2);

    await press("Import the file");
    const bodies = fetchMock.mock.calls
      .filter(([, init]) => init?.method === "POST")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies).toHaveLength(2);
    expect(bodies[1].expected_file_revision).toBe("file-rev-2");
  });

  it("exports through the endpoint rather than a bare link to it", async () => {
    // The export answers only to a bearer header, so a plain <a href> to it
    // would download nothing. The bytes are fetched and handed over as a blob.
    stubExport();
    await mount();

    await press("Export the store");

    const link = screen.getByTestId("drift-export-link");
    expect(link.getAttribute("download")).toBe(
      "autoposter-overrides-2026-09-14T10-11-12-5Z.json",
    );
    // The warning rides with the file rather than standing on its own: the
    // backup panel below carries the same sentence, and two paragraphs of it
    // on one screen is one too many.
    expect(screen.getByText(/Keep it somewhere you would keep a password/)).toBeInTheDocument();
  });

  it("revokes the object URL when it goes away", async () => {
    // Nothing else holds the blob, so the tab keeps it until it is closed.
    stubExport();
    let unmount = () => {};
    await act(async () => {
      unmount = render(<DriftNotice onChanged={() => {}} />).unmount;
    });
    await press("Export the store");

    unmount();

    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:stub");
  });
});
