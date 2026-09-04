import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function mockFetch(responses: Record<string, unknown>) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const body = responses[key] ?? responses[`GET ${url}`];
    if (body === undefined) throw new Error(`unmocked ${key}`);
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
    fireEvent.click(row.querySelector("button")!);

    await waitFor(() => {
      const calls = fetchMock.mock.calls.map(
        ([url, init]) => `${(init as RequestInit)?.method ?? "GET"} ${url}`,
      );
      expect(calls).toContain("PUT /api/items/7/metadata-overrides/studio");
      expect(calls.filter((c) => c === "GET /api/items/7/metadata-overrides")).toHaveLength(2);
    });
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

    expect(await screen.findByRole("status")).toHaveTextContent(/unlocked/i);
    expect(screen.getByRole("status")).toHaveTextContent(/until Plex itself refreshes/i);
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
