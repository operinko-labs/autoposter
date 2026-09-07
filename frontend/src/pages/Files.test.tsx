import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Files } from "./Files";

const OVERLAYS = {
  files: [
    { name: "overlay.png", size: 33365, modified: "2026-09-07T10:11:12+00:00",
      protected: false, referenced_by: ["artwork.poster.overlay_file"] },
    { name: "spare.png", size: 100, modified: "2026-09-07T10:11:12+00:00",
      protected: false, referenced_by: [] },
  ],
};

const FONTS = {
  files: [
    { name: "Comfortaa-Medium.ttf", size: 111344, modified: "2026-09-07T10:11:12+00:00",
      protected: true, referenced_by: ["artwork.poster.text.font"] },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Both listings, in the order the page requests them. */
function listings() {
  return vi
    .fn()
    .mockResolvedValueOnce(json(OVERLAYS))
    .mockResolvedValueOnce(json(FONTS));
}

beforeEach(() => {
  setToken(null);
});

describe("Files", () => {
  it("lists overlays and fonts as two sections", async () => {
    vi.stubGlobal("fetch", listings());

    render(<Files />);

    expect(await screen.findByText("overlay.png")).toBeInTheDocument();
    expect(screen.getByText("Comfortaa-Medium.ttf")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Overlays" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Fonts" })).toBeInTheDocument();
  });

  it("names who references a file and offers no delete for it", async () => {
    vi.stubGlobal("fetch", listings());

    render(<Files />);

    const row = (await screen.findByText("overlay.png")).closest("tr")!;
    expect(within(row).getByText(/artwork\.poster\.overlay_file/)).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /delete/i })).toBeNull();
  });

  it("offers no delete for a file that ships with the service", async () => {
    vi.stubGlobal("fetch", listings());

    render(<Files />);

    const row = (await screen.findByText("Comfortaa-Medium.ttf")).closest("tr")!;
    expect(within(row).queryByRole("button", { name: /delete/i })).toBeNull();
  });

  it("asks to confirm before deleting, then reloads both listings", async () => {
    const fetchMock = listings()
      .mockResolvedValueOnce(json({ status: "deleted", name: "spare.png" }))
      .mockResolvedValueOnce(json({ files: [] }))
      .mockResolvedValueOnce(json(FONTS));
    vi.stubGlobal("fetch", fetchMock);

    render(<Files />);

    const row = (await screen.findByText("spare.png")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    expect(fetchMock.mock.calls[2][0]).toBe("/api/files/overlays/spare.png");
    expect(fetchMock.mock.calls[2][1].method).toBe("DELETE");
  });

  it("cancelling the confirm sends nothing", async () => {
    const fetchMock = listings();
    vi.stubGlobal("fetch", fetchMock);

    render(<Files />);

    const row = (await screen.findByText("spare.png")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "Confirm delete" })).toBeNull();
  });

  it("shows the server's own refusal sentence", async () => {
    const fetchMock = listings().mockResolvedValueOnce(
      json({ detail: "the running configuration still names that file" }, 409),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Files />);

    const row = (await screen.findByText("spare.png")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

    expect(
      await screen.findByText("the running configuration still names that file"),
    ).toBeInTheDocument();
  });

  it("uploads the chosen file as multipart and reloads", async () => {
    const fetchMock = listings()
      .mockResolvedValueOnce(json({ status: "stored", name: "new.png" }))
      .mockResolvedValueOnce(json(OVERLAYS))
      .mockResolvedValueOnce(json(FONTS));
    vi.stubGlobal("fetch", fetchMock);

    render(<Files />);
    await screen.findByText("overlay.png");

    const input = screen.getByLabelText("Add an overlay PNG");
    fireEvent.change(input, {
      target: { files: [new File([new Uint8Array([1, 2, 3])], "new.png", { type: "image/png" })] },
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    const [url, init] = fetchMock.mock.calls[2];
    expect(url).toBe("/api/files/overlays");
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    // `apiFetch`'s FormData branch: the browser writes `Content-Type` itself,
    // with the boundary parameter, and a header set here would replace it with
    // one that has none.
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("refuses a file over the cap without asking the server", async () => {
    const fetchMock = listings();
    vi.stubGlobal("fetch", fetchMock);

    render(<Files />);
    await screen.findByText("overlay.png");

    const huge = new File([""], "huge.png", { type: "image/png" });
    Object.defineProperty(huge, "size", { value: 50 * 1024 * 1024 + 1 });
    fireEvent.change(screen.getByLabelText("Add an overlay PNG"), {
      target: { files: [huge] },
    });

    expect(await screen.findByText(/exceeds the size cap/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows the server's refusal when the name is already taken", async () => {
    const fetchMock = listings().mockResolvedValueOnce(
      json({ detail: "a file of that name is already there; delete it first" }, 409),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Files />);
    await screen.findByText("overlay.png");

    fireEvent.change(screen.getByLabelText("Add an overlay PNG"), {
      target: { files: [new File(["x"], "overlay.png", { type: "image/png" })] },
    });

    expect(
      await screen.findByText("a file of that name is already there; delete it first"),
    ).toBeInTheDocument();
  });

  it("every control is type=button", async () => {
    vi.stubGlobal("fetch", listings());
    const { container } = render(<Files />);
    await screen.findByText("overlay.png");

    const buttons = Array.from(container.querySelectorAll("button"));
    expect(buttons.length).toBeGreaterThan(0);
    expect(buttons.every((button) => button.getAttribute("type") === "button")).toBe(true);
  });
});
