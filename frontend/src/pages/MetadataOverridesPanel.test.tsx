import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MetadataOverridesPanel } from "./MetadataOverridesPanel";

const ENABLED = {
  enabled: true,
  kind: "movie",
  writable: ["critic_rating", "studio", "tagline"],
  overrides: [
    { field: "tagline", value: "A crime saga", updated_at: "2026-09-05T10:00:00Z" },
  ],
};

/** Tags a mocked response as a refusal, so `mockFetch` can answer `ok: false`
 * with the server's own JSON shape (`{"detail": "..."}`) instead of a 200. */
function errorResponse(status: number, detail: string) {
  return { __error: true as const, status, detail };
}

function isErrorResponse(
  value: unknown,
): value is { __error: true; status: number; detail: string } {
  return typeof value === "object" && value !== null && "__error" in value;
}

function mockFetch(responses: Record<string, unknown>) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const body = responses[key] ?? responses[`GET ${url}`];
    if (body === undefined) throw new Error(`unmocked ${key}`);
    if (isErrorResponse(body)) {
      return {
        ok: false, status: body.status, json: async () => ({ detail: body.detail }),
      } as unknown as Response;
    }
    return {
      ok: true, status: 200, json: async () => body,
    } as unknown as Response;
  });
}

describe("MetadataOverridesPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("lists the item's overrides and the fields its kind can carry", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
    }));

    render(<MetadataOverridesPanel itemId={7} />);

    expect(await screen.findByText("A crime saga")).toBeInTheDocument();
    expect(screen.getByText("critic_rating")).toBeInTheDocument();
    expect(screen.getByText("studio")).toBeInTheDocument();
  });

  it("never pre-fills an un-overridden field with a current value", async () => {
    /* The freezing hazard, from the panel's side. The server serves NAMES for
     * `writable`; the panel must not go and fetch what Plex or the facts row
     * currently holds to fill them in, because that would put today's values
     * one Save away from being frozen as overrides. */
    const fetchMock = mockFetch({ "GET /api/items/7/metadata-overrides": ENABLED });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const inputs = screen.getAllByRole("textbox");
    const unoverridden = inputs.filter((i) => (i as HTMLInputElement).value === "");
    expect(unoverridden.length).toBeGreaterThan(0);
  });

  it("saves a value and re-reads the listing rather than patching state", async () => {
    const fetchMock = mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
      "PUT /api/items/7/metadata-overrides/studio": {
        status: "saved", field: "studio", value: "A24", queued: true,
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    const row = screen.getByTestId("override-studio");
    fireEvent.change(row.querySelector("input")!, { target: { value: "A24" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));
    fireEvent.click(within(row).getByRole("button", { name: "Confirm save" }));

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(
        ([url, init]) => `${(init as RequestInit)?.method ?? "GET"} ${url}`,
      );
      expect(calls).toContain("PUT /api/items/7/metadata-overrides/studio");
      expect(calls.filter((c) => c === "GET /api/items/7/metadata-overrides")).toHaveLength(2);
    });
  });

  it("arms Save on the first click but sends nothing until confirmed", async () => {
    const fetchMock = mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
      "PUT /api/items/7/metadata-overrides/studio": {
        status: "saved", field: "studio", value: "A24", queued: true,
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    const row = screen.getByTestId("override-studio");
    fireEvent.change(row.querySelector("input")!, { target: { value: "A24" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));

    // The mutation proof. With the gate removed, Save posts here and this
    // assertion reds: only the initial listing fetch may have gone out.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/items/7/metadata-overrides");

    const confirm = within(row).getByRole("alert");
    expect(confirm).toHaveTextContent("Write studio to Plex and lock it?");
    expect(confirm).toHaveTextContent("A24");
    expect(within(row).getByRole("button", { name: "Confirm save" })).toHaveFocus();
  });

  it("cancels a Save without sending a request", async () => {
    const fetchMock = mockFetch({ "GET /api/items/7/metadata-overrides": ENABLED });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    const row = screen.getByTestId("override-studio");
    fireEvent.change(row.querySelector("input")!, { target: { value: "A24" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));
    fireEvent.click(within(row).getByRole("button", { name: "Cancel" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(within(row).queryByRole("button", { name: "Confirm save" })).toBeNull();
    expect(within(row).getByRole("button", { name: "Save" })).toBeInTheDocument();
  });

  it("clears an override and says what Plex does next, both halves", async () => {
    const fetchMock = mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
      "DELETE /api/items/7/metadata-overrides/tagline": {
        status: "cleared", field: "tagline", unlocked: true, queued: true,
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    fireEvent.click(screen.getByRole("button", { name: /clear tagline/i }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm clear" }));

    expect(await screen.findByRole("status")).toHaveTextContent(/unlocked/i);
    expect(screen.getByRole("status")).toHaveTextContent(/until Plex itself refreshes/i);
  });

  it("arms Clear on the first click but sends nothing until confirmed", async () => {
    const fetchMock = mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
      "DELETE /api/items/7/metadata-overrides/tagline": {
        status: "cleared", field: "tagline", unlocked: true, queued: true,
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    fireEvent.click(screen.getByRole("button", { name: /clear tagline/i }));

    // The mutation proof. With the gate removed, Clear posts here and this
    // assertion reds: only the initial listing fetch may have gone out.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/items/7/metadata-overrides");

    const confirm = screen.getByRole("alert");
    expect(confirm).toHaveTextContent("Clear tagline?");
    expect(confirm).toHaveTextContent("Plex is unlocked");
    expect(screen.getByRole("button", { name: "Confirm clear" })).toHaveFocus();
  });

  it("cancels a Clear without sending a request", async () => {
    const fetchMock = mockFetch({ "GET /api/items/7/metadata-overrides": ENABLED });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    fireEvent.click(screen.getByRole("button", { name: /clear tagline/i }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Confirm clear" })).toBeNull();
    expect(screen.getByRole("button", { name: /clear tagline/i })).toBeInTheDocument();
  });

  it("says Plex was not touched when clearing an exempt item's override", async () => {
    /* N-1: the server skips the Plex write for an exempt item and answers
     * `plex: "skipped (exempt)"` instead of `unlocked: true`. The ordinary
     * "unlocked … rewritten on the next pass" claim is false for that item --
     * the field stays locked and no provider write will ever reach it while
     * the item stays exempt -- so the panel must say something different. */
    const fetchMock = mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
      "DELETE /api/items/7/metadata-overrides/tagline": {
        status: "cleared", field: "tagline", plex: "skipped (exempt)", queued: true,
      },
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    fireEvent.click(screen.getByRole("button", { name: /clear tagline/i }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm clear" }));

    const note = await screen.findByRole("status");
    expect(note).toHaveTextContent(/not touched/i);
    expect(note).toHaveTextContent(/exempt/i);
    expect(note).not.toHaveTextContent(/unlocked/i);
  });

  it("labels a refused PUT with the field name, not the bare class name", async () => {
    /* The server's 422 is deliberately class-name-only (`OverrideValueError`,
     * never the value or a message naming the field) -- see
     * `api/item_overrides.py`'s module docstring. The panel owes the operator
     * the half the server left out. */
    const fetchMock = mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
      "PUT /api/items/7/metadata-overrides/critic_rating": errorResponse(
        422, "OverrideValueError",
      ),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    const row = screen.getByTestId("override-critic_rating");
    fireEvent.change(row.querySelector("input")!, { target: { value: "eleven" } });
    fireEvent.click(within(row).getByRole("button", { name: "Save" }));
    fireEvent.click(within(row).getByRole("button", { name: "Confirm save" }));

    const message = await screen.findByText(/critic_rating: value not accepted/i);
    expect(message).toHaveTextContent("OverrideValueError");
  });

  it("labels a refused DELETE as a failed Plex write, naming the exception class", async () => {
    const fetchMock = mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
      "DELETE /api/items/7/metadata-overrides/tagline": errorResponse(503, "OSError"),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MetadataOverridesPanel itemId={7} />);
    await screen.findByText("A crime saga");

    fireEvent.click(screen.getByRole("button", { name: /clear tagline/i }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm clear" }));

    expect(await screen.findByText(/Plex write failed \(OSError\)/)).toBeInTheDocument();
  });

  it("is read-only with a banner naming the key when the gate is off", async () => {
    vi.stubGlobal("fetch", mockFetch({
      "GET /api/items/7/metadata-overrides": { ...ENABLED, enabled: false },
    }));

    render(<MetadataOverridesPanel itemId={7} />);

    expect(
      await screen.findByText(/operations\.item_overrides_enabled/),
    ).toBeInTheDocument();
    expect(screen.getByText(/left in place and ignored/i)).toBeInTheDocument();
    for (const button of screen.queryAllByRole("button")) {
      expect(button).toBeDisabled();
    }
  });

  it("states the affected count as one item, with no preview", async () => {
    /* C5: an override is not a config edit, so there is nothing for
     * `config/impact.py` to walk and no `_render_affecting` transition to
     * detect. The count is the literal 1. */
    vi.stubGlobal("fetch", mockFetch({
      "GET /api/items/7/metadata-overrides": ENABLED,
    }));

    render(<MetadataOverridesPanel itemId={7} />);

    expect(await screen.findByText(/1 item/)).toBeInTheDocument();
  });
});
