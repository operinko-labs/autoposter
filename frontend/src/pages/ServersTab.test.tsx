/** The Servers tab: which cards are on it, which of them open themselves,
 * what the switches are set to, and what happens after a card writes.
 *
 * The stub routes by URL AND method, like the card's own tests: the tab
 * mounts cards that reach nine routes between them, and anything a test has
 * not declared answers a 500 that names it -- which is how a request this tab
 * should not have made (a catch-up read for a card that is closed, a second
 * listing nobody asked for) shows up as a failure rather than as a passing
 * test.
 *
 * An accordion renders nothing while it is closed, so "the card is open" is
 * asserted through a field only its body carries -- the address -- rather
 * than through the header, which is there either way.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ServersTab, cardsToOpen } from "./ServersTab";
import { ApiError } from "../api/client";
import type { ServerRow } from "../api/servers";
import type { ConfigResponse } from "../api/types";
import { PENDING_EDITS_NOTE } from "./RestartBanner";
import {
  click,
  countOf,
  json,
  router as stubFetch,
  type Call,
  type Route,
} from "./testRouter";

const NOTHING: ServerRow[] = [
  {
    name: "plex",
    configured: false,
    url: null,
    excluded_libraries: [],
    credential_source: "unset",
    restart_pending: false,
    health: { ok: null, detail: null, checked_at: null },
  },
  {
    name: "jellyfin",
    configured: false,
    url: null,
    excluded_libraries: [],
    credential_source: "unset",
    restart_pending: false,
    health: { ok: null, detail: null, checked_at: null },
  },
];

const PLEX_ONLY: ServerRow[] = [
  {
    name: "plex",
    configured: true,
    url: "http://plex:32400",
    excluded_libraries: [],
    credential_source: "stored",
    restart_pending: false,
    health: { ok: true, detail: null, checked_at: null },
  },
  NOTHING[1],
];

const JELLYFIN_NEEDS_A_KEY: ServerRow[] = [
  PLEX_ONLY[0],
  {
    name: "jellyfin",
    configured: true,
    url: "http://jf:8096",
    excluded_libraries: [],
    credential_source: "unset",
    restart_pending: false,
    health: { ok: null, detail: null, checked_at: null },
  },
];

const BOTH: ServerRow[] = [
  PLEX_ONLY[0],
  { ...JELLYFIN_NEEDS_A_KEY[1], credential_source: "stored" },
];

/** A configured deployment whose Plex card needs attention, so the card the
 * switch assertions read is open without a press. */
const PLEX_NEEDS_A_KEY: ServerRow[] = [
  { ...PLEX_ONLY[0], credential_source: "unset" },
  NOTHING[1],
];

/** A served configuration with one of Plex's two switches on and the other
 * off, so "the box shows what the document says" cannot pass by rendering
 * every box the same way. */
const CONFIG: ConfigResponse = {
  badges: { upload_to_plex: true },
  operations: { write_to_plex: false },
};

/** The shared stub, with both cards' opening reads seeded.
 *
 * A configured card makes one when its accordion opens, and which cards those
 * are is what half of this file is about. */
function router(routes: Record<string, Route>): Call[] {
  return stubFetch({
    "GET /api/servers/plex/catch-up": () => json({ run: null }),
    "GET /api/servers/jellyfin/catch-up": () => json({ run: null }),
    ...routes,
  });
}

/** The listing route, which every test declares. */
function listing(servers: ServerRow[]): Record<string, Route> {
  return { "GET /api/servers": () => json({ servers }) };
}

async function renderTab({
  config = CONFIG,
  revision = "r1",
  pendingEdits = false,
  onChanged = () => {},
}: {
  config?: ConfigResponse | null;
  revision?: string | null;
  pendingEdits?: boolean;
  onChanged?: () => void | Promise<void>;
} = {}) {
  await act(async () => {
    render(
      <ServersTab
        config={config}
        revision={revision}
        pendingEdits={pendingEdits}
        onChanged={onChanged}
      />,
    );
  });
}

beforeEach(() => {
  // The tab holds its open cards itself rather than in the page's remembered
  // section, and one test proves it by reading this back -- so a key left
  // behind by another file's window would be asserted as this tab's.
  window.localStorage.clear();
});

describe("cardsToOpen", () => {
  it("opens nothing at all for a listing with no configured server", () => {
    // Which a served page cannot have -- the schema refuses a document with
    // neither block -- and which this tab could not render if it did: a card
    // exists only for a configured server or one picked from Add a server, so
    // a name returned here would open nothing.
    expect(cardsToOpen(NOTHING)).toEqual([]);
  });

  it("opens nothing once a server is configured and complete", () => {
    expect(cardsToOpen(PLEX_ONLY)).toEqual([]);
  });

  it("opens a configured server that is missing its credential", () => {
    expect(cardsToOpen(JELLYFIN_NEEDS_A_KEY)).toEqual(["jellyfin"]);
  });
});

describe("ServersTab", () => {
  it("names each configured server once, and leaves a settled card closed", async () => {
    const calls = router(listing(PLEX_ONLY));
    await renderTab();

    // The accordion's title is the only place the server is named: the card
    // inside carries no heading of its own.
    expect(screen.getAllByRole("button", { name: "Plex" })).toHaveLength(1);
    expect(screen.queryByLabelText("Plex address")).toBeNull();
    // A closed accordion renders no body, so the card is not mounted and
    // reads nothing -- which is the whole point of opening only the cards
    // that need attention, and is a request rather than a rendering.
    expect(countOf(calls, "GET", "/api/servers/plex/catch-up")).toBe(0);
    // An unconfigured server has no card at all -- it is offered under Add a
    // server instead.
    expect(screen.queryByRole("button", { name: "Jellyfin" })).toBeNull();
  });

  it("opens the card of a configured server with no credential", async () => {
    router(listing(JELLYFIN_NEEDS_A_KEY));
    await renderTab();

    expect(screen.getByLabelText("Jellyfin address")).toHaveValue(
      "http://jf:8096",
    );
    // ...and only that one: the complete server is left alone.
    expect(screen.queryByLabelText("Plex address")).toBeNull();
  });

  it("toggles a card by hand without remembering it anywhere", async () => {
    router(listing(PLEX_ONLY));
    await renderTab();

    await click("Plex");
    expect(screen.getByLabelText("Plex address")).toHaveValue(
      "http://plex:32400",
    );
    await click("Plex");
    expect(screen.queryByLabelText("Plex address")).toBeNull();
    // Deliberately not the page's remembered open section: that one is "what
    // I was last reading", and this one is "what needs attention", which must
    // not outlive the attention it needed.
    expect(window.localStorage.getItem("autoposter.settings.open")).toBeNull();
  });

  it("offers only the servers this deployment has not configured", async () => {
    router(listing(PLEX_ONLY));
    await renderTab();

    expect(screen.getByRole("button", { name: "Add Jellyfin" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add Plex" })).toBeNull();
  });

  it("opens an empty card when a server is added, and stops offering it", async () => {
    router(listing(PLEX_ONLY));
    await renderTab();

    await click("Add Jellyfin");
    expect(screen.getByLabelText("Jellyfin address")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Add Jellyfin" })).toBeNull();
  });

  it("shows each switch as the served configuration has it", async () => {
    router(listing(PLEX_NEEDS_A_KEY));
    await renderTab();

    // Read out of the document by their dotted paths, not fetched again: the
    // page already holds it, and a second read is a second answer that can
    // disagree with the tab beside this one.
    expect(
      screen.getByLabelText("Upload badged artwork to Plex"),
    ).toBeChecked();
    expect(screen.getByLabelText("Write metadata to Plex")).not.toBeChecked();
  });

  it("reads a missing switch as off rather than as nothing", async () => {
    router(listing(PLEX_NEEDS_A_KEY));
    await renderTab({ config: {} });

    expect(
      screen.getByLabelText("Upload badged artwork to Plex"),
    ).not.toBeChecked();
  });

  it("reads a leaf that is not a boolean as off, not as truthy", async () => {
    router(listing(PLEX_NEEDS_A_KEY));
    // A document no save from this page could have produced, so the box has
    // to refuse it rather than tick itself on a non-empty string.
    await renderTab({ config: { badges: { upload_to_plex: "yes" } } });

    expect(
      screen.getByLabelText("Upload badged artwork to Plex"),
    ).not.toBeChecked();
  });

  it("re-reads the listing and the page after a card writes", async () => {
    const calls = router({
      ...listing(JELLYFIN_NEEDS_A_KEY),
      "PUT /api/servers/jellyfin/credential": () =>
        json({ name: "jellyfin", credential_source: "stored" }),
    });
    const onChanged = vi.fn();
    await renderTab({ onChanged });

    fireEvent.change(screen.getByLabelText("Jellyfin credential"), {
      target: { value: "a-key" },
    });
    await click("Save Jellyfin credential");

    // Both halves of a card move together: the listing it is rendered from,
    // and the configuration its switches are read out of.
    expect(countOf(calls, "PUT", "/api/servers/jellyfin/credential")).toBe(1);
    expect(countOf(calls, "GET", "/api/servers")).toBe(2);
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("keeps a card's success note when the page's own re-read fails", async () => {
    router({
      ...listing(JELLYFIN_NEEDS_A_KEY),
      "PUT /api/servers/jellyfin/credential": () =>
        json({ name: "jellyfin", credential_source: "stored" }),
    });
    await renderTab({
      onChanged: () =>
        Promise.reject(new ApiError(500, "the configuration could not be read")),
    });

    fireEvent.change(screen.getByLabelText("Jellyfin credential"), {
      target: { value: "a-key" },
    });
    await click("Save Jellyfin credential");

    // The write landed. A re-read that failed afterwards is said here, not
    // thrown back into the card, where it would be rendered as that write's
    // refusal and take the note with it -- and an operator reading that types
    // a stored credential in a second time.
    expect(
      screen.getByText(/Stored Jellyfin's credential/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("the configuration could not be read"),
    ).toBeInTheDocument();
  });

  it("says so when the listing carries no servers at all", async () => {
    // What `fetchServers` answers for a 200 whose body has no list: a blank
    // tab would read as one that had not finished loading.
    router({ "GET /api/servers": () => json({}) });
    await renderTab();

    expect(
      screen.getByText("This deployment listed no servers."),
    ).toBeInTheDocument();
  });

  it("offers the library map only when both servers are configured", async () => {
    router(listing(PLEX_ONLY));
    await renderTab();
    expect(screen.queryByRole("button", { name: "Library map" })).toBeNull();
  });

  it("carries the library map closed, so it reads no libraries unasked", async () => {
    router(listing(BOTH));
    await renderTab();

    expect(
      screen.getByRole("button", { name: "Library map" }),
    ).toBeInTheDocument();
    // Closed until it is opened: three requests, two of them out to a media
    // server, for a section most visits to this tab never open.
    expect(screen.queryByRole("button", { name: "Save the map" })).toBeNull();
  });

  it("says a removed server's credential is still stored, where the card cannot", async () => {
    // The card that made the removal is unmounted by the re-read that follows
    // it -- the listing comes back with that server unconfigured -- so the one
    // durable record that a credential for a server this deployment no longer
    // has survived in the store belongs here.
    let removed = false;
    router({
      "GET /api/servers": () => json({ servers: removed ? PLEX_ONLY : BOTH }),
      "DELETE /api/servers/jellyfin": () => {
        removed = true;
        return json({
          version_before: "a",
          version_after: "b",
          restart_required: [],
          credential_cleared: false,
        });
      },
    });
    await renderTab();

    await click("Jellyfin");
    await click("Remove Jellyfin");
    await click("Yes, remove Jellyfin");

    // The card is gone, and the sentence is not.
    expect(screen.queryByLabelText("Jellyfin address")).toBeNull();
    expect(
      screen.getByText(
        "Removed Jellyfin, but its credential could not be cleared and is " +
          "still stored. Open Jellyfin again from Add a server to clear it.",
      ),
    ).toBeInTheDocument();
    // ...and the advice is something the operator can follow: the server is
    // back under Add a server, which is where a card for it comes from now.
    expect(
      screen.getByRole("button", { name: "Add Jellyfin" }),
    ).toBeInTheDocument();
  });

  it("puts a server added during this visit back under Add a server when it is removed", async () => {
    // `added` is what keeps a picked-but-unconfigured server's card on screen.
    // Left alone, a removal would leave the empty card standing under a
    // sentence telling the operator to open it from Add a server.
    let removed = false;
    router({
      "GET /api/servers": () => json({ servers: removed ? PLEX_ONLY : BOTH }),
      "DELETE /api/servers/jellyfin": () => {
        removed = true;
        return json({
          version_before: "a",
          version_after: "b",
          restart_required: [],
          credential_cleared: true,
        });
      },
    });
    await renderTab();

    await click("Jellyfin");
    await click("Remove Jellyfin");
    await click("Yes, remove Jellyfin");

    expect(screen.getByText("Removed Jellyfin.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Jellyfin address")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Add Jellyfin" }),
    ).toBeInTheDocument();
  });

  it("re-reads only this tab's listing when a catch-up is started", async () => {
    // A catch-up changes no setting, and the page's re-read re-seeds its
    // editor from the server -- so asking for it here would discard an
    // operator's unsaved edit on another tab to refresh something that has
    // not moved.
    const calls = router({
      ...listing(JELLYFIN_NEEDS_A_KEY),
      "POST /api/servers/jellyfin/catch-up": () =>
        json({ run_id: 3, server: "jellyfin", cadence_seconds: 60 }),
    });
    const onChanged = vi.fn();
    await renderTab({ onChanged });

    await click("Catch Jellyfin up");

    expect(countOf(calls, "POST", "/api/servers/jellyfin/catch-up")).toBe(1);
    expect(countOf(calls, "GET", "/api/servers")).toBe(2);
    expect(onChanged).not.toHaveBeenCalled();
    expect(countOf(calls, "GET", "/api/config")).toBe(0);
  });

  it("refuses a card's writes while the page is holding an unsaved edit", async () => {
    router(listing(JELLYFIN_NEEDS_A_KEY));
    await renderTab({ pendingEdits: true });

    expect(screen.getByRole("button", { name: "Save Jellyfin" })).toBeDisabled();
    expect(screen.getByText(PENDING_EDITS_NOTE)).toBeInTheDocument();
    // The read beside it is untouched, and so is the catch-up: neither of
    // them asks the page to re-read.
    expect(
      screen.getByRole("button", { name: "Catch Jellyfin up" }),
    ).toBeEnabled();
  });

  it("gives two open cards' buttons a name each, and names each card's region", async () => {
    // Two configured servers both missing a credential is a state this tab
    // opens by itself, and "Save" beside "Save" is two controls with one name.
    router(
      listing([
        { ...PLEX_ONLY[0], credential_source: "unset" },
        JELLYFIN_NEEDS_A_KEY[1],
      ]),
    );
    await renderTab();

    expect(screen.queryAllByRole("button", { name: "Save" })).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Save Plex" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save Jellyfin" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Plex" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Jellyfin" })).toBeInTheDocument();
  });

  it("says why the listing failed, and retries only when asked", async () => {
    let answer: Route = () => json({ detail: "the database is unreachable" }, 500);
    const calls = router({ "GET /api/servers": (init) => answer(init) });
    await renderTab();

    // The server's own sentence, verbatim.
    expect(screen.getByText("the database is unreachable")).toBeInTheDocument();
    expect(countOf(calls, "GET", "/api/servers")).toBe(1);

    answer = () => json({ servers: PLEX_ONLY });
    await click("Try again");
    expect(screen.getByRole("button", { name: "Plex" })).toBeInTheDocument();
    expect(screen.queryByText("the database is unreachable")).toBeNull();
    expect(countOf(calls, "GET", "/api/servers")).toBe(2);
  });
});
