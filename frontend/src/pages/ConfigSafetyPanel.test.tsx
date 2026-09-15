/** The panel an operator reaches for after a configuration goes missing.
 *
 * It exists because of the 2026-09-01 incident, where the only surviving copy
 * of seventeen overrides was one that happened to be in a chat window. Three
 * things are load-bearing here and each has a test that says which one broke:
 * a restore is offered per snapshot and re-reads afterwards; a destructive
 * restore or import is gated on the operator ticking a box, not on the page
 * guessing; and the export's warning about what the file contains is not
 * decoration.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { STALE_SAVE_NOTE } from "../api/overrides";
import { ConfigSafetyPanel, EXPORT_WARNING } from "./ConfigSafetyPanel";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SNAPSHOTS = [
  { id: 7, created_at: "2026-09-03T14:22:00+00:00", path_count: 3, reason: "save" },
  { id: 6, created_at: "2026-09-03T11:04:00+00:00", path_count: 12, reason: "apply" },
];

interface StubOptions {
  snapshots?: unknown;
  restore?: () => Response;
  exported?: unknown;
  preview?: () => Response;
  imported?: () => Response;
}

function stubFetch(options: StubOptions = {}) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path === "/api/config/snapshots") {
      return json(options.snapshots ?? SNAPSHOTS);
    }
    if (/^\/api\/config\/snapshots\/\d+\/restore$/.test(path)) {
      return options.restore ? options.restore() : json({ version_before: "a", version_after: "b", restart_required: [] });
    }
    if (path === "/api/config/overrides/export") {
      return json(
        options.exported ?? {
          autoposter_overrides: 1,
          exported_at: "2026-09-03T14:30:00+00:00",
          document: { workers: 9 },
        },
      );
    }
    if (path === "/api/config/preview") {
      return options.preview
        ? options.preview()
        : json({
            version_before: "a",
            version_after: "b",
            restart_required: [],
            inert: [],
            impact: null,
          });
    }
    if (path === "/api/config/overrides/import") {
      return options.imported ? options.imported() : json({ version_before: "a", version_after: "b", restart_required: [] });
    }
    throw new Error(`unexpected fetch: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

async function renderPanel(options: StubOptions = {}, revision: string | null = "rev-1") {
  const stub = stubFetch(options);
  const onChanged = vi.fn(async () => {});
  await act(async () => {
    render(<ConfigSafetyPanel revision={revision} onChanged={onChanged} />);
  });
  return { ...stub, onChanged };
}

async function click(name: string | RegExp) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

/** Every row's Restore button has the same accessible name, so a plain
 * `getByRole` is ambiguous the moment there is more than one snapshot -- which
 * the default fixture always has. Scoped to a row instead. */
async function clickRestore(rowIndex = 0) {
  const rows = screen.getAllByRole("listitem");
  await act(async () => {
    fireEvent.click(within(rows[rowIndex]).getByRole("button", { name: /Restore/ }));
  });
}

beforeEach(() => {
  setToken(null);
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:stub"),
    revokeObjectURL: vi.fn(),
  });
});

describe("an unsaved edit on the page", () => {
  it("refuses a restore and an import while the page is holding one", async () => {
    // A restore and an import both replace the whole stored document and then
    // re-seed the page from it, which would throw the typing away without
    // saying so. The same rule, and the same sentence, as the restart button's.
    const { calls } = stubFetch();
    await act(async () => {
      render(
        <ConfigSafetyPanel
          revision="rev-1"
          pendingEdits
          onChanged={vi.fn(async () => {})}
        />,
      );
    });

    const rows = screen.getAllByRole("listitem");
    expect(within(rows[0]).getByRole("button", { name: /Restore/ })).toBeDisabled();
    expect(screen.getByLabelText(/Restore from a backup file/i)).toBeDisabled();
    expect(
      screen.getByText(/Save or discard the changes below first/),
    ).toBeInTheDocument();
    // Taking a backup writes nothing, so it stays available.
    expect(
      screen.getByRole("button", { name: /Download a backup/i }),
    ).toBeEnabled();
    expect(calls.filter((call) => call.init?.method === "POST")).toHaveLength(0);
  });
});

describe("the previous-versions list", () => {
  it("lists each snapshot by how many settings it held and when", async () => {
    await renderPanel();

    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("3 settings");
    expect(rows[1]).toHaveTextContent("12 settings");
  });

  it("says so plainly when there is no history yet", async () => {
    await renderPanel({ snapshots: [] });
    expect(screen.getByText(/No previous versions/i)).toBeInTheDocument();
  });

  it("restores one snapshot, sends the revision, and re-reads afterwards", async () => {
    const { calls, onChanged } = await renderPanel();

    await clickRestore();
    const restore = calls.find((call) => call.path.endsWith("/restore"));
    expect(restore?.init?.method).toBe("POST");
    expect(JSON.parse(String(restore?.init?.body))).toEqual({
      confirm: false,
      expected_revision: "rev-1",
    });
    // Provenance is the server's to report -- the same re-read every other
    // write on this page does.
    expect(onChanged).toHaveBeenCalledTimes(1);
    // And the list itself is refreshed: the restore just added a snapshot.
    expect(calls.filter((call) => call.path === "/api/config/snapshots")).toHaveLength(2);
  });

  it("sends confirm only when the operator ticked the box", async () => {
    const { calls } = await renderPanel();

    fireEvent.click(screen.getByLabelText(/Allow this to remove settings/i));
    await clickRestore();

    const restore = calls.find((call) => call.path.endsWith("/restore"));
    expect(JSON.parse(String(restore?.init?.body)).confirm).toBe(true);
  });

  it("shows the server's refusal instead of restoring silently", async () => {
    await renderPanel({
      restore: () =>
        json(
          {
            detail: [
              {
                path: "document",
                message:
                  "this would drop 11 stored overrides (badges.enabled); send confirm: true to do it deliberately",
              },
            ],
          },
          422,
        ),
    });

    await clickRestore();
    expect(screen.getByText(/would drop 11 stored overrides/)).toBeInTheDocument();
  });

  it("tells the operator and re-reads when a restore is stale", async () => {
    const { onChanged } = await renderPanel({
      restore: () =>
        json({ message: "moved", current_revision: "rev-9", changed_paths: [] }, 409),
    });

    await clickRestore();
    expect(screen.getByText(STALE_SAVE_NOTE)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("surfaces a failed re-read on a stale restore too, instead of claiming the page is current", async () => {
    const onChanged = vi.fn(async () => {
      throw new Error("network dropped");
    });
    stubFetch({
      restore: () =>
        json({ message: "moved", current_revision: "rev-9", changed_paths: [] }, 409),
    });
    await act(async () => {
      render(<ConfigSafetyPanel revision="rev-1" onChanged={onChanged} />);
    });

    // The unhandled-rejection concern: this must not throw out of the click
    // handler even though the re-read itself fails.
    await clickRestore();

    expect(screen.getByText("network dropped")).toBeInTheDocument();
    // The claim that "the page now shows the current settings" would be a
    // lie here -- the re-read that would make it true never completed.
    expect(screen.queryByText(STALE_SAVE_NOTE)).not.toBeInTheDocument();
  });

  it("re-submits a refused restore with confirm once the operator ticks the box, carrying the same snapshot", async () => {
    let restoreCalls = 0;
    const { calls } = await renderPanel({
      restore: () => {
        restoreCalls += 1;
        return restoreCalls === 1
          ? json(
              {
                detail: [
                  {
                    path: "document",
                    message: "this would drop 11 stored overrides; send confirm: true to do it deliberately",
                  },
                ],
              },
              422,
            )
          : json({ version_before: "a", version_after: "b", restart_required: [] });
      },
    });

    await clickRestore(0);
    expect(screen.getByText(/would drop 11 stored overrides/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/Allow this to remove settings/i));
    await clickRestore(0);

    const restores = calls.filter((call) => call.path.endsWith("/restore"));
    expect(restores).toHaveLength(2);
    // Same snapshot both times.
    expect(restores[0].path).toBe(restores[1].path);
    expect(JSON.parse(String(restores[0].init?.body))).toEqual({
      confirm: false,
      expected_revision: "rev-1",
    });
    expect(JSON.parse(String(restores[1].init?.body))).toEqual({
      confirm: true,
      expected_revision: "rev-1",
    });
    expect(screen.queryByText(/would drop 11 stored overrides/)).not.toBeInTheDocument();
  });

  it("does not carry a confirm tick into a later restore -- it resets after every submit, win or lose", async () => {
    let restoreCalls = 0;
    const { calls } = await renderPanel({
      restore: () => {
        restoreCalls += 1;
        return restoreCalls === 1
          ? json({ version_before: "a", version_after: "b", restart_required: [] })
          : json(
              {
                detail: [
                  { path: "document", message: "this would drop 4 stored overrides; send confirm: true" },
                ],
              },
              422,
            );
      },
    });

    fireEvent.click(screen.getByLabelText(/Allow this to remove settings/i));
    await clickRestore(0);
    // The tick that pushed the first restore through does not survive it.
    expect(screen.getByLabelText(/Allow this to remove settings/i)).not.toBeChecked();

    await clickRestore(0);
    expect(screen.getByText(/this would drop 4 stored overrides/)).toBeInTheDocument();

    const restores = calls.filter((call) => call.path.endsWith("/restore"));
    expect(restores).toHaveLength(2);
    expect(JSON.parse(String(restores[1].init?.body)).confirm).toBe(false);
  });
});

describe("export", () => {
  it("warns what the file holds before offering it", async () => {
    await renderPanel();
    expect(screen.getByText(EXPORT_WARNING)).toBeInTheDocument();
    // The warning is the trade the endpoint made, stated where the operator
    // makes the decision. A paraphrase would be a different promise.
    expect(EXPORT_WARNING).toMatch(/notification URL/i);
  });

  it("downloads the envelope under a named file", async () => {
    const { calls } = await renderPanel();

    await click(/Download a backup/);

    expect(calls.some((call) => call.path === "/api/config/overrides/export")).toBe(true);
    const link = screen.getByTestId("config-export-link") as HTMLAnchorElement;
    expect(link.getAttribute("download")).toMatch(/^autoposter-overrides-.*\.json$/);
    expect(link.getAttribute("href")).toBe("blob:stub");
  });
});

describe("import", () => {
  const FILE = new File(
    [JSON.stringify({ autoposter_overrides: 1, exported_at: "x", document: { workers: 9 } })],
    "backup.json",
    { type: "application/json" },
  );

  async function choose(file: File) {
    const input = screen.getByLabelText(/Restore from a backup file/i);
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });
  }

  it("previews before offering the import, and imports nothing yet", async () => {
    const { calls } = await renderPanel();

    await choose(FILE);

    const preview = calls.find((call) => call.path === "/api/config/preview");
    expect(JSON.parse(String(preview?.init?.body))).toEqual({
      document: { workers: 9 },
      expected_revision: "rev-1",
    });
    expect(calls.some((call) => call.path === "/api/config/overrides/import")).toBe(false);
    expect(screen.getByRole("button", { name: /Import these settings/i })).toBeInTheDocument();
  });

  it("sends the whole envelope on import, and re-reads", async () => {
    const { calls, onChanged } = await renderPanel();

    await choose(FILE);
    await click(/Import these settings/i);

    const imported = calls.find((call) => call.path === "/api/config/overrides/import");
    expect(JSON.parse(String(imported?.init?.body))).toEqual({
      autoposter_overrides: 1,
      exported_at: "x",
      document: { workers: 9 },
      confirm: false,
      expected_revision: "rev-1",
    });
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("refuses a file that is not an export, without calling the server", async () => {
    // The server refuses it too, but a page that posted a Plex library dump
    // and waited for a 422 would be telling the operator the server is fussy
    // rather than that they picked the wrong file.
    const { calls } = await renderPanel();

    await choose(new File(["{\"workers\": 9}"], "config.json", { type: "application/json" }));

    expect(screen.getByText(/not an Autoposter overrides backup/i)).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/config/preview")).toBe(false);
  });

  it("refuses a file that is not JSON at all", async () => {
    const { calls } = await renderPanel();

    await choose(new File(["not json"], "notes.txt", { type: "text/plain" }));

    expect(screen.getByText(/could not be read as JSON/i)).toBeInTheDocument();
    expect(calls.some((call) => call.path === "/api/config/preview")).toBe(false);
  });

  it("shows the server's refusal of an import rather than claiming success", async () => {
    await renderPanel({
      imported: () =>
        json({ detail: [{ path: "workers", message: "Input should be a valid integer" }] }, 422),
    });

    await choose(FILE);
    await click(/Import these settings/i);

    expect(screen.getByText(/workers: Input should be a valid integer/)).toBeInTheDocument();
  });

  it("renders a FastAPI-shaped 422 (loc/msg) readably instead of [object Object]", async () => {
    await renderPanel({
      imported: () =>
        json(
          {
            detail: [
              { loc: ["body", "document", "foo"], msg: "Extra inputs are not permitted", type: "extra_forbidden" },
            ],
          },
          422,
        ),
    });

    await choose(FILE);
    await click(/Import these settings/i);

    expect(screen.getByText("foo: Extra inputs are not permitted")).toBeInTheDocument();
    expect(screen.queryByText(/\[object Object\]/)).not.toBeInTheDocument();
  });

  it("re-submits a refused import with confirm once the operator ticks the box, carrying the same approved envelope", async () => {
    let importCalls = 0;
    const { calls } = await renderPanel({
      imported: () => {
        importCalls += 1;
        return importCalls === 1
          ? json(
              {
                detail: [
                  {
                    path: "document",
                    message: "this would drop 11 stored overrides; send confirm: true to do it deliberately",
                  },
                ],
              },
              422,
            )
          : json({ version_before: "a", version_after: "b", restart_required: [] });
      },
    });

    await choose(FILE);
    await click(/Import these settings/i);
    expect(screen.getByText(/would drop 11 stored overrides/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/Allow this to remove settings/i));
    await click(/Import these settings/i);

    const imports = calls.filter((call) => call.path === "/api/config/overrides/import");
    expect(imports).toHaveLength(2);
    expect(JSON.parse(String(imports[0].init?.body))).toEqual({
      autoposter_overrides: 1,
      exported_at: "x",
      document: { workers: 9 },
      confirm: false,
      expected_revision: "rev-1",
    });
    expect(JSON.parse(String(imports[1].init?.body))).toEqual({
      autoposter_overrides: 1,
      exported_at: "x",
      document: { workers: 9 },
      confirm: true,
      expected_revision: "rev-1",
    });
    expect(screen.queryByText(/would drop 11 stored overrides/)).not.toBeInTheDocument();
  });
});
