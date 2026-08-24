/** The run-modes page.
 *
 * Two things are pinned here that no type can pin. The first is the
 * confirmation gate: every one of these modes writes to Plex (or over the
 * backup tree), and "Apply" must not be one misplaced click. So the assertion
 * is not merely "confirming applies" -- it is that pressing Apply on its own
 * fires **no** request at all. Delete the gate and that assertion reds.
 *
 * The second is the honest copy. The counters these endpoints return are
 * coarser than they look (`uploaded` includes items whose marker write failed;
 * `reset` counts fields, not items) and two of the modes have consequences the
 * numbers never mention (restore idles the worker pool; reset cannot delete the
 * upload it replaces). Those sentences are the feature as much as the buttons
 * are, so they are asserted rather than left to drift.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setToken } from "../api/client";
import { Modes } from "./Modes";

const FILTERS = {
  libraries: ["Movies", "TV"],
  kinds: ["movie", "show", "season", "episode"],
  statuses: ["rendered", "no_art"],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type ModeHandler = (path: string, init?: RequestInit) => Promise<Response>;

/** The filters endpoint always answers; every mode trigger goes to `onMode`,
 * which defaults to a plausible dry-run body so a test that cares only about
 * the request does not have to invent a response. */
function stubFetch(onMode?: ModeHandler) {
  const fetchMock = vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/items/filters") return json(FILTERS);
    if (path.startsWith("/api/artwork-modes/")) {
      if (onMode) return onMode(path, init);
      return json({ mode: "restore", status: "dry run", dry_run: true, items: 0 });
    }
    throw new Error(`the page requested an unexpected path: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The body one mode trigger was posted with, parsed. */
function postedBody(fetchMock: ReturnType<typeof stubFetch>, call = 0): unknown {
  const [, init] = fetchMock.mock.calls.filter(([path]) =>
    String(path).startsWith("/api/artwork-modes/"),
  )[call];
  return JSON.parse(String((init as RequestInit).body));
}

/** Every mode is a named region, so a control is always addressed inside the
 * card it belongs to -- six cards carry a "Library" select and a "Dry run"
 * button, and an unscoped query would find all six. */
async function card(name: string): Promise<HTMLElement> {
  return await screen.findByRole("region", { name });
}

async function renderModes() {
  const result = render(<Modes />);
  // The filters request resolves on the first tick; awaiting one card means
  // no test races the dropdowns being populated.
  await card("Backup");
  return result;
}

beforeEach(() => {
  setToken("a-session-token");
});

afterEach(() => {
  setToken(null);
  vi.unstubAllGlobals();
});

describe("the mode list", () => {
  it("offers all six operator-triggered modes", async () => {
    stubFetch();

    await renderModes();

    for (const name of [
      "Backup",
      "Restore",
      "Remove overlays",
      "Reset to Plex artwork",
      "Logo updater",
      "Logo revert",
    ]) {
      expect(await card(name)).toBeInTheDocument();
    }
  });

  it("populates every filtered mode's controls from /api/items/filters", async () => {
    stubFetch();

    await renderModes();

    const restore = await card("Restore");
    const library = within(restore).getByLabelText("Library");
    expect(within(library as HTMLSelectElement).getByRole("option", { name: "Movies" }))
      .toBeInTheDocument();
    const type = within(restore).getByLabelText("Type");
    expect(within(type as HTMLSelectElement).getByRole("option", { name: "episode" }))
      .toBeInTheDocument();
    expect(within(restore).getByLabelText("Item ID")).toBeInTheDocument();
  });

  it("offers the logo modes only the kinds a clearlogo can belong to", async () => {
    stubFetch();

    await renderModes();

    // A clearlogo belongs to a movie or a show and to nothing below one, so a
    // season selected here would return zero items with no explanation.
    const type = within(await card("Logo updater")).getByLabelText("Type");
    const options = within(type as HTMLSelectElement).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "All types",
      "movie",
      "show",
    ]);
  });

  it("gives the backup mode no dry run, because it never writes to Plex", async () => {
    stubFetch();

    await renderModes();

    const backup = await card("Backup");
    expect(within(backup).queryByRole("button", { name: "Dry run" })).toBeNull();
    expect(within(backup).getByRole("button", { name: "Run backup" })).toBeInTheDocument();
  });
});

describe("the honest copy each mode carries", () => {
  it("says the reset unlocks both fields and cannot delete what it replaces", async () => {
    stubFetch();
    await renderModes();

    const reset = await card("Reset to Plex artwork");
    const text = reset.textContent ?? "";
    expect(text).toContain("poster and the background");
    expect(text).toContain("Plex's own agent artwork");
    // The binding sentence: the replaced image is NOT reclaimed.
    expect(text).toContain("cannot delete");
    expect(text).toContain("orphaned upload:// image");
    // And the counters are per field, not per item.
    expect(text).toContain("count (item, field) pairs, not items");
  });

  it("says a restore idles the worker pipeline while it runs", async () => {
    stubFetch();
    await renderModes();

    const text = (await card("Restore")).textContent ?? "";
    expect(text).toContain("pauses the whole worker pipeline");
    expect(text).toContain("jobs already in flight finish");
  });

  it("presents the logo updater's first-run refusal as the expected step", async () => {
    stubFetch();
    await renderModes();

    const text = (await card("Logo updater")).textContent ?? "";
    expect(text).toContain("plausibility cap");
    expect(text).toContain("not a failure");
    expect(text).toContain("Raise the cap or narrow the filters");
    // The two coarse counters, said plainly rather than implied.
    expect(text).toContain("including any whose marker write afterwards failed");
    expect(text).toContain("no provider had one, the only candidate was an SVG");
  });

  it("says the logo revert touches only logos it set and Plex still shows", async () => {
    stubFetch();
    await renderModes();

    const text = (await card("Logo revert")).textContent ?? "";
    expect(text).toContain("only the logos this service set");
    expect(text).toContain("still has selected");
    expect(text).toContain("never touched");
  });

  it("warns that a backup overwrites whatever the backup tree already holds", async () => {
    stubFetch();
    await renderModes();

    const text = (await card("Backup")).textContent ?? "";
    expect(text).toContain("overwrites");
    expect(text).toContain("Nothing on Plex is touched");
  });
});

describe("dry run", () => {
  it("fires the trigger with apply false and shows the counts it reports", async () => {
    const fetchMock = stubFetch(async () =>
      json({
        mode: "restore",
        status: "dry run",
        dry_run: true,
        items: 120,
        items_with_backup: 34,
        files: 51,
      }),
    );

    await renderModes();
    const restore = await card("Restore");
    fireEvent.click(within(restore).getByRole("button", { name: "Dry run" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [path, init] = fetchMock.mock.calls[1];
    expect(path).toBe("/api/artwork-modes/restore");
    expect((init as RequestInit).method).toBe("POST");
    expect(postedBody(fetchMock)).toEqual({ apply: false });

    // The server's numbers, under the labels that say what they count.
    const result = within(restore).getByRole("status");
    expect(within(result).getByText("120")).toBeInTheDocument();
    expect(within(result).getByText("items matched")).toBeInTheDocument();
    expect(within(result).getByText("34")).toBeInTheDocument();
    expect(within(result).getByText("items with a file in the backup tree")).toBeInTheDocument();
    expect(within(result).getByText("51")).toBeInTheDocument();
    expect(result.textContent).toContain("Nothing was changed");
  });

  it("sends the filters the operator chose", async () => {
    const fetchMock = stubFetch();

    await renderModes();
    const revert = await card("Remove overlays");
    fireEvent.change(within(revert).getByLabelText("Type"), { target: { value: "movie" } });
    fireEvent.change(within(revert).getByLabelText("Library"), { target: { value: "Movies" } });
    fireEvent.change(within(revert).getByLabelText("Item ID"), { target: { value: "42" } });
    fireEvent.click(within(revert).getByRole("button", { name: "Dry run" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/artwork-modes/revert");
    expect(postedBody(fetchMock)).toEqual({
      apply: false,
      type: "movie",
      library: "Movies",
      item_id: 42,
    });
  });

  it("keeps one mode's filters out of another's request", async () => {
    const fetchMock = stubFetch();

    await renderModes();
    fireEvent.change(within(await card("Restore")).getByLabelText("Library"), {
      target: { value: "TV" },
    });
    fireEvent.click(
      within(await card("Remove overlays")).getByRole("button", { name: "Dry run" }),
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(postedBody(fetchMock)).toEqual({ apply: false });
  });
});

describe("the confirmation gate", () => {
  it("fires nothing when Apply is pressed on its own", async () => {
    const fetchMock = stubFetch();

    await renderModes();
    const reset = await card("Reset to Plex artwork");
    fireEvent.click(within(reset).getByRole("button", { name: "Apply…" }));

    // The mutation proof. With the gate removed, Apply posts here and this
    // assertion reds: only the filters request may have gone out.
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/items/filters");

    // What the operator gets instead is the question.
    expect(within(reset).getByRole("button", { name: "Confirm apply" })).toBeInTheDocument();
    expect(within(reset).getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(reset.textContent).toContain("This writes to Plex");
  });

  it("fires the trigger with apply true once confirmed", async () => {
    const fetchMock = stubFetch(async () =>
      json({
        mode: "reset",
        status: "reset",
        dry_run: false,
        items: 30,
        items_with_our_art: 12,
        fields: 20,
        reset: 18,
        failed: 2,
        note: "the artwork this replaced stays on the Plex server",
      }),
    );

    await renderModes();
    const reset = await card("Reset to Plex artwork");
    fireEvent.click(within(reset).getByRole("button", { name: "Apply…" }));
    fireEvent.click(within(reset).getByRole("button", { name: "Confirm apply" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/artwork-modes/reset");
    expect(postedBody(fetchMock)).toEqual({ apply: true });

    const result = within(reset).getByRole("status");
    expect(within(result).getByText("18")).toBeInTheDocument();
    expect(within(result).getByText("fields reset")).toBeInTheDocument();
    expect(result.textContent).toContain("the artwork this replaced stays on the Plex server");
    expect(result.textContent).not.toContain("Nothing was changed");

    // The gate closes behind the run, so the next Apply asks again.
    expect(within(reset).queryByRole("button", { name: "Confirm apply" })).toBeNull();
  });

  it("drops out of the gate on Cancel without firing", async () => {
    const fetchMock = stubFetch();

    await renderModes();
    const restore = await card("Restore");
    fireEvent.click(within(restore).getByRole("button", { name: "Apply…" }));
    fireEvent.click(within(restore).getByRole("button", { name: "Cancel" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(within(restore).queryByRole("button", { name: "Confirm apply" })).toBeNull();
    expect(within(restore).getByRole("button", { name: "Apply…" })).toBeInTheDocument();
  });

  it("gates one mode at a time", async () => {
    stubFetch();

    await renderModes();
    fireEvent.click(within(await card("Restore")).getByRole("button", { name: "Apply…" }));
    fireEvent.click(
      within(await card("Logo revert")).getByRole("button", { name: "Apply…" }),
    );

    // Arming a second mode disarms the first: two live confirm buttons on one
    // page is exactly the misclick this gate exists to prevent.
    expect(
      within(await card("Restore")).queryByRole("button", { name: "Confirm apply" }),
    ).toBeNull();
    expect(
      within(await card("Logo revert")).getByRole("button", { name: "Confirm apply" }),
    ).toBeInTheDocument();
  });

  it("disarms the gate and clears the stale result when a filter changes", async () => {
    const fetchMock = stubFetch(async () =>
      json({ mode: "reset", status: "dry run", dry_run: true, items: 30 }),
    );

    await renderModes();
    const reset = await card("Reset to Plex artwork");
    fireEvent.change(within(reset).getByLabelText("Library"), { target: { value: "Movies" } });
    fireEvent.click(within(reset).getByRole("button", { name: "Dry run" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(within(reset).getByText("30")).toBeInTheDocument();

    fireEvent.click(within(reset).getByRole("button", { name: "Apply…" }));
    expect(within(reset).getByRole("button", { name: "Confirm apply" })).toBeInTheDocument();

    // Changing the filter the grant was for must revoke it: a change to what
    // would actually be sent must not leave a live "Confirm apply" behind it.
    fireEvent.change(within(reset).getByLabelText("Library"), { target: { value: "TV" } });

    expect(within(reset).queryByRole("button", { name: "Confirm apply" })).toBeNull();
    expect(within(reset).getByRole("button", { name: "Apply…" })).toBeInTheDocument();
    // And the stale dry-run counts, taken against a filter that no longer
    // applies, must go with it.
    expect(within(reset).queryByRole("status")).toBeNull();

    // The filter change itself fired no request -- only the dry run above
    // went out after the initial filters load.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe("what comes back", () => {
  it("renders a refusal as the refusal it is, with its numbers", async () => {
    stubFetch(async () =>
      json({
        mode: "logo",
        status: "refused",
        dry_run: true,
        items: 4000,
        items_missing_logo: 3200,
        reason:
          "refused: 3200 of 4000 item(s) would change (80%), more than the safety " +
          "cap of 25%; this usually means a filter, a mount or the backup tree is " +
          "wrong -- change nothing",
      }),
    );

    await renderModes();
    const logo = await card("Logo updater");
    fireEvent.click(within(logo).getByRole("button", { name: "Dry run" }));

    const result = await within(logo).findByRole("status");
    // A refusal is an answer, not an error: it keeps its counts and its
    // reason, and it is never shown in the error slot.
    expect(within(result).getByText("refused")).toBeInTheDocument();
    expect(within(result).getByText("3200")).toBeInTheDocument();
    expect(within(result).getByText("items missing a logo")).toBeInTheDocument();
    expect(result.textContent).toContain("more than the safety cap of 25%");
    expect(within(logo).queryByRole("alert")).toBeNull();
  });

  it("shows a failed request in the mode that made it, and nowhere else", async () => {
    stubFetch(async () => json({ detail: "this instance is not connected to Plex" }, 503));

    await renderModes();
    const restore = await card("Restore");
    fireEvent.click(within(restore).getByRole("button", { name: "Dry run" }));

    expect(await within(restore).findByRole("alert")).toHaveTextContent(
      "this instance is not connected to Plex",
    );
    expect(within(await card("Logo updater")).queryByRole("alert")).toBeNull();
  });

  it("reports a filters outage without disabling the modes themselves", async () => {
    // The dropdowns are a convenience; an unfiltered whole-library run is
    // still a legitimate thing to ask for, so the outage is a note, not a
    // dead page.
    const fetchMock = vi.fn(async (path: string) => {
      if (path === "/api/items/filters") return json({ detail: "filters are down" }, 500);
      return json({ mode: "backup", status: "backed up", items: 1, written: 2 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Modes />);

    expect(await screen.findByText("Filters are unavailable: filters are down"))
      .toBeInTheDocument();
    expect(
      within(await card("Backup")).getByRole("button", { name: "Run backup" }),
    ).toBeEnabled();
  });

  it("posts no filter keys for the backup mode, which takes none", async () => {
    const fetchMock = stubFetch(async () =>
      json({ mode: "backup", status: "backed up", items: 9, written: 17, skipped: 1, failed: 0 }),
    );

    await renderModes();
    const backup = await card("Backup");
    fireEvent.click(within(backup).getByRole("button", { name: "Run backup" }));
    fireEvent.click(within(backup).getByRole("button", { name: "Confirm backup" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toBe("/api/artwork-modes/backup");
    expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBeUndefined();

    const result = within(backup).getByRole("status");
    expect(within(result).getByText("17")).toBeInTheDocument();
    expect(within(result).getByText("artwork files written")).toBeInTheDocument();
    // No dry run to distinguish, so no "nothing was changed" claim either.
    expect(result.textContent).not.toContain("Nothing was changed");
  });
});
