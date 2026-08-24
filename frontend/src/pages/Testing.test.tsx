import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Testing } from "./Testing";

const KINDS = ["poster", "season_poster", "background", "title_card"];
const LENGTHS = ["short", "medium", "long"];

function imageResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "image/jpeg" },
  });
}

function truncatedResponse(artKind: string, length: string): Response {
  return new Response(JSON.stringify({ truncated: true, art_kind: artKind, length }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function errorResponse(detail: string, status = 503): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The one POST route the page makes, answered from a per-test handler that
 * sees the parsed body so it can key its answer on the requested kind/length.
 * Anything else throws, naming the path. */
function stubFetch(handler: (body: { art_kind: string; length: string }) => Response) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path !== "/api/testing/sample") {
      throw new Error(`the page requested an unexpected path: ${path}`);
    }
    return handler(JSON.parse(init!.body as string));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Object URL -> the blob it was made from, so a rendered sample can be proved
 * to show the bytes its own request returned. Mirrors ItemDetail's panes. */
const objectUrls = new Map<string, Blob>();
const realCreateObjectURL = URL.createObjectURL;
const realRevokeObjectURL = URL.revokeObjectURL;

/** The <td> for one kind × length cell, located by the row's header and the
 * length column's position -- not by DOM order alone, so a page that laid the
 * grid out transposed could not pass. */
function cell(kind: string, length: string): HTMLElement {
  const rowHeader = screen.getByText(kind, { selector: "th" });
  const row = rowHeader.closest("tr");
  if (row === null) throw new Error(`no row for ${kind}`);
  const cells = [...row.querySelectorAll("td")];
  return cells[LENGTHS.indexOf(length)];
}

function renderButton(kind: string, length: string): HTMLElement {
  return within(cell(kind, length)).getByRole("button");
}

async function bytesShownBy(element: HTMLElement): Promise<string> {
  const image = element.querySelector("img");
  if (image === null) throw new Error("the cell shows no image");
  const blob = objectUrls.get(image.getAttribute("src") ?? "");
  if (blob === undefined) throw new Error("the image's src is not an object URL");
  return await blob.text();
}

beforeEach(() => {
  setToken(null);
  objectUrls.clear();
  URL.createObjectURL = vi.fn((blob: Blob) => {
    const url = `blob:sample-${objectUrls.size + 1}`;
    objectUrls.set(url, blob);
    return url;
  });
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  URL.createObjectURL = realCreateObjectURL;
  URL.revokeObjectURL = realRevokeObjectURL;
  vi.restoreAllMocks();
});

describe("Testing", () => {
  it("lays out a cell for every art kind and length", () => {
    stubFetch(() => imageResponse("x"));

    render(<Testing />);

    for (const kind of KINDS) {
      expect(screen.getByText(kind, { selector: "th" })).toBeInTheDocument();
    }
    for (const length of LENGTHS) {
      expect(screen.getByText(length, { selector: "th" })).toBeInTheDocument();
    }
    // A Render control per cell: four kinds by three lengths.
    expect(screen.getAllByRole("button")).toHaveLength(KINDS.length * LENGTHS.length);
  });

  it("notes that samples reflect the running config", () => {
    stubFetch(() => imageResponse("x"));

    render(<Testing />);

    // The page's one claim about itself: a sample uses the config the process
    // is running now, so an edit in Settings shows up on the next render here.
    const note = document.querySelector(".testing-note")?.textContent ?? "";
    expect(note).toContain("Settings");
    expect(note.toLowerCase()).toContain("running");
  });

  it("renders a cell by posting its kind and length, and shows the returned bytes", async () => {
    const fetchMock = stubFetch((body) =>
      imageResponse(`bytes-${body.art_kind}-${body.length}`),
    );

    render(<Testing />);
    fireEvent.click(renderButton("background", "medium"));

    await waitFor(() => expect(cell("background", "medium").querySelector("img")).not.toBeNull());

    const post = fetchMock.mock.calls.find((call) => call[0] === "/api/testing/sample");
    expect(post).toBeDefined();
    expect(post![1]?.method).toBe("POST");
    expect(JSON.parse(post![1]!.body as string)).toEqual({
      art_kind: "background",
      length: "medium",
    });
    // The exact bytes the request returned, via an object URL -- not a bare
    // <img src> that could not carry the bearer header the endpoint needs.
    expect(await bytesShownBy(cell("background", "medium"))).toBe("bytes-background-medium");
  });

  it("renders a truncated response as a labelled outcome, not an error", async () => {
    // The long title cannot fit, so the pipeline produces no artifact and the
    // endpoint says so as JSON. This is an OUTCOME the operator asked to see;
    // coercing it into the error branch would falsely claim the render failed.
    stubFetch((body) => truncatedResponse(body.art_kind, body.length));

    render(<Testing />);
    fireEvent.click(renderButton("poster", "long"));

    await waitFor(() =>
      expect(cell("poster", "long").querySelector(".sample-truncated")).not.toBeNull(),
    );

    const target = cell("poster", "long");
    expect(target.querySelector(".sample-truncated")!.textContent).toContain("did not fit");
    // Not an error, and not a broken image.
    expect(target.querySelector(".sample-error")).toBeNull();
    expect(target.querySelector("img")).toBeNull();
  });

  it("shows the server's message when a render fails", async () => {
    stubFetch(() => errorResponse("no HTTP client on this instance"));

    render(<Testing />);
    fireEvent.click(renderButton("poster", "short"));

    await waitFor(() =>
      expect(cell("poster", "short").querySelector(".sample-error")).not.toBeNull(),
    );
    expect(cell("poster", "short").querySelector(".sample-error")!.textContent).toBe(
      "no HTTP client on this instance",
    );
    expect(cell("poster", "short").querySelector(".sample-truncated")).toBeNull();
  });

  it("revokes the sample's object URL on unmount", async () => {
    stubFetch(() => imageResponse("bytes"));

    const view = render(<Testing />);
    fireEvent.click(renderButton("poster", "short"));
    await waitFor(() => expect(cell("poster", "short").querySelector("img")).not.toBeNull());

    const url = cell("poster", "short").querySelector("img")!.getAttribute("src");
    view.unmount();

    // The decoded image is held by the document until someone lets go of it.
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(url);
  });

  it("scrolls the grid inside its own container", async () => {
    // The mobile convention: a table too wide for a phone gets its own
    // horizontal scroller, so the page column never scrolls sideways.
    stubFetch(() => imageResponse("x"));

    render(<Testing />);
    await act(async () => {});

    const table = document.querySelector(".testing-grid");
    expect(table).not.toBeNull();
    expect(table!.parentElement).toHaveClass("table-scroll");
  });
});
