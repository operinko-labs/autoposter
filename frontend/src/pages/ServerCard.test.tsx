/** One server's card. A form, because a server is a unit: address, credential
 * and exclusions belong together.
 *
 * Nothing here puts a typed credential anywhere a failure could print it: the
 * one typed value is asserted against the stubbed request body and nowhere
 * else, and everything else asserted is a name, a state or a sentence.
 *
 * The stub is the one the three Servers-tab suites share (`testRouter.ts`):
 * it routes by URL AND method, because a card reaches nine routes, three of
 * them at one address with different verbs (the catch-up trio) and two of them
 * at the same verb on different addresses (the two PUTs). Anything a test has
 * not declared answers a 500 that names it, which is how a request the card
 * should not have made shows up as a failure rather than as a passing test.
 *
 * Every button on the card is named for the server it acts on -- "Save Plex",
 * not "Save" -- because two cards are open together in the states this tab is
 * built for, so that is how they are pressed here.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ServerCard } from "./ServerCard";
import { RESTART_NOTE } from "../api/overrides";
import type { ServerRemovalResult, ServerRow } from "../api/servers";
import { PENDING_EDITS_NOTE } from "./RestartBanner";
import {
  bodyOf,
  click,
  json,
  router as stubFetch,
  type Call,
  type Route,
} from "./testRouter";

const PLEX: ServerRow = {
  name: "plex",
  configured: true,
  url: "http://plex:32400",
  excluded_libraries: ["Photos"],
  credential_source: "stored",
  restart_pending: false,
  health: { ok: true, detail: "Plex answered.", checked_at: "2026-09-14T09:00:00Z" },
};

const JELLYFIN: ServerRow = {
  name: "jellyfin",
  configured: false,
  url: null,
  excluded_libraries: [],
  credential_source: "unset",
  restart_pending: false,
  health: { ok: null, detail: null, checked_at: null },
};

/** What each switch is set to now, keyed by the dotted path the page reads it
 * out of the served configuration by. One of the five is on, so the delta a
 * save states has something to be a delta against in both directions. */
const SWITCH_VALUES: Record<string, boolean> = {
  "badges.upload_to_plex": false,
  "operations.write_to_plex": true,
  "badges.upload_to_jellyfin": false,
  "operations.write_to_jellyfin": false,
  "jellyfin.replace_thumb_with_backdrop": false,
};

const LIBRARIES = {
  libraries: [
    { id: "1", name: "Movies", kind: "movie" },
    { id: "2", name: "Photos", kind: "photo" },
  ],
};

const SAVED = {
  version_before: "a",
  version_after: "b",
  restart_required: ["plex"],
};

const OPEN_RUN = {
  run_id: 7,
  server: "plex",
  status: "running",
  started_at: "2026-09-14T09:00:00Z",
  finished_at: null,
  cadence_seconds: 60,
  detail: null,
  due: 120,
  done: 3,
  failed: 1,
  total: 124,
};

/** The sentence `api/servers.py` refuses a first save with, verbatim. */
const NEEDS_A_CREDENTIAL_FIRST =
  "set this server's credential before saving it; a configured server " +
  "without one sends the deployment back to first-start setup";

/** The shared stub, with this card's opening read seeded.
 *
 * A configured card makes that read when it opens, so it is declared here
 * rather than in every test -- keyed off the server's name, so a card for
 * either server gets the named answer rather than the undeclared-route 500 as
 * a surprise. A test that cares about that read declares its own. */
function router(routes: Record<string, Route>, name = "plex"): Call[] {
  return stubFetch({
    [`GET /api/servers/${name}/catch-up`]: () => json({ run: null }),
    ...routes,
  });
}

async function renderCard(
  server: ServerRow = PLEX,
  {
    revision = "r1",
    switches = SWITCH_VALUES,
    pendingEdits = false,
    onChanged = () => {},
    onRemoved = () => {},
  }: {
    revision?: string | null;
    switches?: Record<string, boolean>;
    pendingEdits?: boolean;
    onChanged?: () => void;
    onRemoved?: (result: ServerRemovalResult) => void;
  } = {},
) {
  await act(async () => {
    render(
      <ServerCard
        server={server}
        switches={switches}
        revision={revision}
        pendingEdits={pendingEdits}
        onChanged={onChanged}
        onRemoved={onRemoved}
      />,
    );
  });
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("ServerCard", () => {
  it("shows the address, the credential's source and the connection pill", async () => {
    router({});
    await renderCard();
    expect(screen.getByLabelText("Plex address")).toHaveValue("http://plex:32400");
    expect(screen.getByText("credential: stored")).toBeInTheDocument();
    expect(screen.getByText("connected")).toBeInTheDocument();
  });

  it("renders an unreachable server's pill rather than claiming it is up", async () => {
    router({});
    await renderCard({
      ...PLEX,
      health: {
        ok: false,
        detail: "Plex could not be reached (ConnectError).",
        checked_at: null,
      },
    });
    expect(screen.getByText("unreachable")).toBeInTheDocument();
  });

  it("says when a saved server is not the one this deployment is running on", async () => {
    router({});
    await renderCard({ ...PLEX, restart_pending: true });
    expect(screen.getByText("restart to apply")).toBeInTheDocument();
  });

  it("checks the connection with the typed address and the typed credential", async () => {
    const calls = router({
      "POST /api/servers/plex/check": () =>
        json({
          ok: true,
          refused: false,
          failure: null,
          version: null,
          detail: "Plex answered.",
        }),
    });
    await renderCard();
    type("Plex address", "http://elsewhere:32400");
    type("Plex credential", "typed");
    await click("Check Plex connection");
    expect(bodyOf(calls, "POST", "/api/servers/plex/check")).toEqual({
      url: "http://elsewhere:32400",
      credential_value: "typed",
    });
    expect(screen.getByText("Plex answered.")).toBeInTheDocument();
    // Sent, so it is gone: the field never holds a value past the request
    // that carried it.
    expect(screen.getByLabelText("Plex credential")).toHaveValue("");
  });

  it("sends an empty body when neither field has been edited", async () => {
    // Which is what asks the deployment to use the address it booted with and
    // the credential it holds -- the one pairing the server will send a
    // stored credential to.
    const calls = router({
      "POST /api/servers/plex/check": () =>
        json({
          ok: true,
          refused: false,
          failure: null,
          version: null,
          detail: "Plex answered.",
        }),
    });
    await renderCard();
    await click("Check Plex connection");
    expect(bodyOf(calls, "POST", "/api/servers/plex/check")).toEqual({});
  });

  it("refuses to probe a typed address with an empty credential field", async () => {
    // That body is a 400 every time -- the deployment will not send a
    // credential it holds to an address a request named -- so the card does
    // not offer the press, and says which half is missing.
    router({});
    await renderCard();
    type("Plex address", "http://elsewhere:32400");
    expect(screen.getByRole("button", { name: "Check Plex connection" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reload Plex libraries" })).toBeDisabled();
    expect(
      screen.getByText(/must come with the credential to use against it/),
    ).toBeInTheDocument();
    type("Plex credential", "typed");
    expect(screen.getByRole("button", { name: "Check Plex connection" })).toBeEnabled();
  });

  it("puts the version the server answered with into the pill", async () => {
    router({
      "POST /api/servers/plex/check": () =>
        json({
          ok: true,
          refused: false,
          failure: null,
          version: "1.41.0.8992",
          detail: "Plex answered.",
        }),
    });
    await renderCard();
    await click("Check Plex connection");
    expect(screen.getByText("connected, version 1.41.0.8992")).toBeInTheDocument();
  });

  it("drops a probe result when the address it was about is edited", async () => {
    // The pill is a state, and leaving that one up beside a different address
    // would have it report a host nothing has contacted.
    router({
      "POST /api/servers/plex/check": () =>
        json({
          ok: true,
          refused: false,
          failure: null,
          version: "1.41.0.8992",
          detail: "Plex answered.",
        }),
    });
    await renderCard();
    await click("Check Plex connection");
    expect(screen.getByText("connected, version 1.41.0.8992")).toBeInTheDocument();
    type("Plex address", "http://elsewhere:32400");
    expect(screen.queryByText("connected, version 1.41.0.8992")).not.toBeInTheDocument();
    expect(screen.getByText("connected")).toBeInTheDocument();
  });

  it("reloads the libraries and ticks the ones that are not excluded", async () => {
    router({ "POST /api/servers/plex/libraries": () => json(LIBRARIES) });
    await renderCard();
    await click("Reload Plex libraries");
    expect(screen.getByLabelText("Movies")).toBeChecked();
    expect(screen.getByLabelText("Photos")).not.toBeChecked();
  });

  it("shows the server's own sentence when the library read is refused", async () => {
    router({
      "POST /api/servers/plex/libraries": () =>
        json({ detail: "Plex refused the credential." }, 502),
    });
    await renderCard();
    await click("Reload Plex libraries");
    expect(screen.getByText("Plex refused the credential.")).toBeInTheDocument();
  });

  it("saves the address, the complement of the tick-list, and the switches", async () => {
    const calls = router({
      "POST /api/servers/plex/libraries": () => json(LIBRARIES),
      "PUT /api/servers/plex": () => json(SAVED),
    });
    await renderCard();
    await click("Reload Plex libraries");
    fireEvent.click(screen.getByLabelText("Movies"));
    await click("Save Plex");
    expect(bodyOf(calls, "PUT", "/api/servers/plex")).toEqual({
      url: "http://plex:32400",
      // The stored exclusion first, then the one just unticked: the list is
      // seeded from what is stored and appended to, so a library the server
      // has stopped listing keeps its exclusion.
      excluded_libraries: ["Photos", "Movies"],
      // No box was moved, so the save states nothing about the switches.
      switches: {},
      expected_revision: "r1",
      confirm: false,
    });
  });

  it("shows each switch as it is set now, and sends the one that was moved", async () => {
    const calls = router({ "PUT /api/servers/plex": () => json(SAVED) });
    await renderCard();
    expect(screen.getByLabelText("Write metadata to Plex")).toBeChecked();
    expect(screen.getByLabelText("Upload badged artwork to Plex")).not.toBeChecked();
    fireEvent.click(screen.getByLabelText("Upload badged artwork to Plex"));
    await click("Save Plex");
    expect(
      (bodyOf(calls, "PUT", "/api/servers/plex") as { switches: unknown }).switches,
    ).toEqual({ "badges.upload_to_plex": true });
  });

  it("states nothing about a switch put back where it was", async () => {
    // The route merges what is sent over the stored block, so a path sent for
    // a switch nobody changed is a write nobody asked for.
    const calls = router({ "PUT /api/servers/plex": () => json(SAVED) });
    await renderCard();
    fireEvent.click(screen.getByLabelText("Write metadata to Plex"));
    fireEvent.click(screen.getByLabelText("Write metadata to Plex"));
    await click("Save Plex");
    expect(
      (bodyOf(calls, "PUT", "/api/servers/plex") as { switches: unknown }).switches,
    ).toEqual({});
  });

  it("says a check is refused until the restart, and what the response says waits", async () => {
    router({ "PUT /api/servers/plex": () => json(SAVED) });
    await renderCard();
    await click("Save Plex");
    // Refused outright, not merely aimed at the old address: the booted block
    // is what a probe with an empty body resolves against, and a first save
    // leaves it with none.
    expect(
      screen.getByText(/connection check is refused until it has restarted/),
    ).toBeInTheDocument();
    expect(screen.getByText(new RegExp(RESTART_NOTE))).toBeInTheDocument();
  });

  it("claims no restart when the write's own answer says nothing waits", async () => {
    // `restart_required` is this write's difference against the running
    // generation, and a note that hard-coded the sentence would send an
    // operator to restart a deployment for a change already in force.
    router({
      "PUT /api/servers/plex": () => json({ ...SAVED, restart_required: [] }),
    });
    await renderCard();
    await click("Save Plex");
    expect(screen.getByText(/^Saved Plex\./)).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(RESTART_NOTE))).not.toBeInTheDocument();
  });

  it("says a save that changed the managed libraries also starts a catch-up", async () => {
    // `swap_config` reports a server whose exclusions moved and the scheduler
    // starts the backlog for it, so a save is a catch-up nobody pressed a
    // button for -- and one that then refuses the next press of Catch up.
    router({
      "POST /api/servers/plex/libraries": () => json(LIBRARIES),
      "PUT /api/servers/plex": () => json(SAVED),
    });
    await renderCard();
    await click("Reload Plex libraries");
    fireEvent.click(screen.getByLabelText("Movies"));
    await click("Save Plex");
    expect(
      screen.getByText(/also starts a catch-up for Plex/),
    ).toBeInTheDocument();
  });

  it("says nothing about a catch-up when the managed libraries did not move", async () => {
    router({ "PUT /api/servers/plex": () => json(SAVED) });
    await renderCard();
    await click("Save Plex");
    expect(screen.queryByText(/starts a catch-up/)).not.toBeInTheDocument();
  });

  it("offers no save at all when the page was served no revision", async () => {
    // Both writes require one, so a card that sent the body anyway would be
    // trading a disabled button for a guaranteed refusal.
    router({});
    await renderCard(PLEX, { revision: null });
    expect(screen.getByRole("button", { name: "Save Plex" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove Plex" })).toBeDisabled();
  });

  it("puts the credential field first on a server that is not configured", async () => {
    router({}, "jellyfin");
    await renderCard(JELLYFIN);
    const credential = screen.getByLabelText("Jellyfin credential");
    const address = screen.getByLabelText("Jellyfin address");
    expect(
      credential.compareDocumentPosition(address) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders the server's refusal to save a server that has no credential", async () => {
    router(
      {
        "PUT /api/servers/jellyfin": () =>
          json({ detail: NEEDS_A_CREDENTIAL_FIRST }, 409),
      },
      "jellyfin",
    );
    await renderCard(JELLYFIN);
    type("Jellyfin address", "http://jf:8096");
    await click("Save Jellyfin");
    expect(screen.getByText(NEEDS_A_CREDENTIAL_FIRST)).toBeInTheDocument();
  });

  it("stores a typed credential through its own route and clears the field", async () => {
    const calls = router({
      "PUT /api/servers/plex/credential": () =>
        json({ name: "plex", credential_source: "stored" }),
    });
    await renderCard();
    type("Plex credential", "a-token");
    await click("Save Plex credential");
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.url)).toEqual([
      "/api/servers/plex/credential",
    ]);
    expect(screen.getByLabelText("Plex credential")).toHaveValue("");
  });

  it("names the layer that takes over when a stored credential is cleared", async () => {
    // The route answers the layer now supplying the value rather than a fixed
    // word, so the card must not claim the server has no credential.
    const calls = router({
      "DELETE /api/servers/plex/credential": () =>
        json({
          name: "plex",
          credential_source: "environment",
          restart_required: true,
        }),
    });
    await renderCard();
    await click("Clear Plex credential");
    expect(
      calls.filter((call) => call.method === "DELETE").map((call) => call.url),
    ).toEqual(["/api/servers/plex/credential"]);
    expect(screen.getByText(/It now comes from the environment/)).toBeInTheDocument();
  });

  it("says so when nothing takes over a cleared credential", async () => {
    router({
      "DELETE /api/servers/plex/credential": () =>
        json({ name: "plex", credential_source: "unset", restart_required: false }),
    });
    await renderCard();
    await click("Clear Plex credential");
    expect(screen.getByText(/No other source supplies it/)).toBeInTheDocument();
  });

  it("says nothing has caught up yet for the empty envelope", async () => {
    // `{"run": null}` is a fact, not a failed read, and the card says so
    // rather than leaving the operator to guess which it was.
    router({});
    await renderCard();
    expect(screen.getByText("No catch-up has run for Plex yet.")).toBeInTheDocument();
  });

  it("starts a catch-up and then reports what it is working through", async () => {
    let reads = 0;
    const calls = router({
      "GET /api/servers/plex/catch-up": () =>
        json(reads++ === 0 ? { run: null } : OPEN_RUN),
      "POST /api/servers/plex/catch-up": () =>
        json({ run_id: 7, server: "plex", cadence_seconds: 60 }),
    });
    await renderCard();
    expect(screen.getByText("No catch-up has run for Plex yet.")).toBeInTheDocument();
    await click("Catch Plex up");
    expect(
      calls.filter((call) => call.method === "POST").map((call) => call.url),
    ).toEqual(["/api/servers/plex/catch-up"]);
    expect(screen.getByText(/120 due/)).toBeInTheDocument();
    // A run that is still open is one that can be called off.
    expect(screen.getByRole("button", { name: "Cancel Plex catch-up" })).toBeInTheDocument();
  });

  it("calls off a run in flight and re-reads what it left behind", async () => {
    let reads = 0;
    const calls = router({
      "GET /api/servers/plex/catch-up": () =>
        json(
          reads++ === 0
            ? OPEN_RUN
            : {
                ...OPEN_RUN,
                status: "cancelled",
                finished_at: "2026-09-14T09:05:00Z",
              },
        ),
      "DELETE /api/servers/plex/catch-up": () =>
        json({
          run_id: 7,
          restored: 2,
          removed: 1,
          detail: "cancelled: 124 marked, 3 done, 1 failed; 2 restored, 1 removed",
        }),
    });
    await renderCard();
    await click("Cancel Plex catch-up");
    expect(
      calls.filter((call) => call.method === "DELETE").map((call) => call.url),
    ).toEqual(["/api/servers/plex/catch-up"]);
    expect(screen.getByText(/2 restored, 1 removed/)).toBeInTheDocument();
    expect(screen.getByText(/Catch up cancelled/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Cancel Plex catch-up" }),
    ).not.toBeInTheDocument();
  });

  it("retries this server's failed rows through its own route", async () => {
    const calls = router({
      "POST /api/servers/plex/retry-failed": () =>
        json({ server: "plex", artwork: 4, metadata: 2 }),
    });
    await renderCard();
    await click("Retry Plex's failed deliveries");
    expect(
      calls.filter((call) => call.method === "POST").map((call) => call.url),
    ).toEqual(["/api/servers/plex/retry-failed"]);
    expect(screen.getByText(/Re-armed 4 artwork and 2 metadata/)).toBeInTheDocument();
  });

  it("asks before removing a server, and sends a body with the confirmation", async () => {
    const calls = router({
      "DELETE /api/servers/plex": () => json({ ...SAVED, credential_cleared: true }),
    });
    await renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Remove Plex" }));
    await click("Yes, remove Plex");
    const removal = calls.find((call) => call.method === "DELETE");
    // A bodyless DELETE is refused by the request validator before the
    // handler runs, so the body is sent even when both fields are defaults.
    expect(removal?.sentBody).toBe(true);
    expect(removal?.body).toEqual({ expected_revision: "r1", confirm: true });
  });

  it("hands the removal's answer up rather than writing it on itself", async () => {
    // The re-read that follows takes this card off the screen, so a sentence
    // written here is one nobody can read. What the removal answered goes to
    // the tab, which outlives it -- and one of the two sentences is the only
    // record that a credential for a server this deployment no longer has is
    // still in the store.
    const onRemoved = vi.fn();
    router({
      "DELETE /api/servers/plex": () => json({ ...SAVED, credential_cleared: false }),
    });
    await renderCard(PLEX, { onRemoved });
    fireEvent.click(screen.getByRole("button", { name: "Remove Plex" }));
    await click("Yes, remove Plex");
    expect(onRemoved).toHaveBeenCalledWith(
      expect.objectContaining({ credential_cleared: false }),
    );
    expect(
      screen.queryByText(/could not be cleared/),
    ).not.toBeInTheDocument();
  });

  it("shows the server's refusal to be removed rather than retrying", async () => {
    const refusal =
      "this is the only configured media server; configure another one before removing it";
    router({ "DELETE /api/servers/plex": () => json({ detail: refusal }, 409) });
    await renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Remove Plex" }));
    await click("Yes, remove Plex");
    expect(screen.getByText(refusal)).toBeInTheDocument();
  });

  it("takes the values a re-read brings when nothing is being edited", async () => {
    // The page re-reads for reasons that are not this card's own save, and a
    // card still holding what it was mounted with would write that stale list
    // back over somebody else's change: `excluded_libraries` is a
    // replacement, and the revision it sends is the fresh one.
    router({});
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(
        <ServerCard
          server={PLEX}
          switches={SWITCH_VALUES}
          revision="r1"
          onChanged={() => {}}
          onRemoved={() => {}}
        />,
      );
    });
    await act(async () => {
      view.rerender(
        <ServerCard
          server={{ ...PLEX, url: "http://moved:32400" }}
          switches={{ ...SWITCH_VALUES, "badges.upload_to_plex": true }}
          revision="r2"
          onChanged={() => {}}
          onRemoved={() => {}}
        />,
      );
    });
    expect(screen.getByLabelText("Plex address")).toHaveValue("http://moved:32400");
    expect(screen.getByLabelText("Upload badged artwork to Plex")).toBeChecked();
  });

  it("leaves an edit in flight alone when the page re-reads underneath it", async () => {
    router({});
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(
        <ServerCard
          server={PLEX}
          switches={SWITCH_VALUES}
          revision="r1"
          onChanged={() => {}}
          onRemoved={() => {}}
        />,
      );
    });
    type("Plex address", "http://being-typed:32400");
    await act(async () => {
      view.rerender(
        <ServerCard
          server={{ ...PLEX, url: "http://moved:32400" }}
          switches={SWITCH_VALUES}
          revision="r2"
          onChanged={() => {}}
          onRemoved={() => {}}
        />,
      );
    });
    expect(screen.getByLabelText("Plex address")).toHaveValue("http://being-typed:32400");
  });

  it("has the page re-read after each of its three writes", async () => {
    // The whole contract between this card and the tab: the card writes one
    // server, the page re-reads the servers and the configuration.
    const onChanged = vi.fn();
    router({
      "PUT /api/servers/plex/credential": () =>
        json({ name: "plex", credential_source: "stored" }),
      "PUT /api/servers/plex": () => json(SAVED),
      "DELETE /api/servers/plex": () => json({ ...SAVED, credential_cleared: true }),
    });
    await renderCard(PLEX, { onChanged });
    type("Plex credential", "a-token");
    await click("Save Plex credential");
    expect(onChanged).toHaveBeenCalledTimes(1);
    await click("Save Plex");
    expect(onChanged).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Remove Plex" }));
    await click("Yes, remove Plex");
    expect(onChanged).toHaveBeenCalledTimes(3);
  });

  it("refuses every write while the page is holding an unsaved edit", async () => {
    // Each of them ends in a re-read that re-seeds the page's editor from the
    // server, which would throw that edit away with nothing on screen to say
    // so -- the rule the restart banner and the two System-tab panels keep.
    router({});
    await renderCard(PLEX, { pendingEdits: true });
    type("Plex credential", "a-token");
    for (const name of [
      "Save Plex",
      "Remove Plex",
      "Save Plex credential",
      "Clear Plex credential",
    ]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    // The reads are untouched: neither of them writes anything, so neither
    // has an edit to lose.
    expect(screen.getByRole("button", { name: "Check Plex connection" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reload Plex libraries" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Catch Plex up" })).toBeEnabled();
    expect(screen.getAllByText(PENDING_EDITS_NOTE)).toHaveLength(1);
  });

  it("names its own region and its buttons after the server they act on", async () => {
    // Two cards are open together in the states this tab is built for, and
    // "Save" beside "Save" is two controls with one name.
    router({});
    await renderCard();
    expect(screen.getByRole("region", { name: "Plex" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save Plex" })).toHaveTextContent(
      "Save",
    );
    expect(
      screen.getByRole("button", { name: "Check Plex connection" }),
    ).toHaveTextContent("Check connection");
  });

  it("does not offer a second catch-up over a run nothing has closed", async () => {
    // The service takes one lock per server and refuses a second start in so
    // many words, so that press beside the Cancel button is a guaranteed
    // refusal.
    router({ "GET /api/servers/plex/catch-up": () => json(OPEN_RUN) });
    await renderCard();
    expect(screen.queryByRole("button", { name: "Catch Plex up" })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Cancel Plex catch-up" }),
    ).toBeInTheDocument();
  });

  it("offers a catch-up again once the last run has finished", async () => {
    router({
      "GET /api/servers/plex/catch-up": () =>
        json({ ...OPEN_RUN, status: "ok", finished_at: "2026-09-14T09:05:00Z" }),
    });
    await renderCard();
    expect(
      screen.getByRole("button", { name: "Catch Plex up" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel Plex catch-up" })).toBeNull();
  });

  it("reads an open run's counts again while it is open, and stops when it closes", async () => {
    // The counts are the one thing on this card that moves without anybody
    // pressing anything, and the press that used to look like it would
    // refresh them is the one the service refuses.
    vi.useFakeTimers();
    try {
      let reads = 0;
      const calls = router({
        "GET /api/servers/plex/catch-up": () => {
          reads += 1;
          return json(
            reads < 3
              ? { ...OPEN_RUN, done: reads * 10 }
              : {
                  ...OPEN_RUN,
                  status: "ok",
                  finished_at: "2026-09-14T09:05:00Z",
                  done: 124,
                },
          );
        },
      });
      await act(async () => {
        render(
          <ServerCard
            server={PLEX}
            switches={SWITCH_VALUES}
            revision="r1"
            onChanged={() => {}}
            onRemoved={() => {}}
          />,
        );
      });
      expect(screen.getByText(/10 done/)).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(screen.getByText(/20 done/)).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(screen.getByText(/124 done/)).toBeInTheDocument();
      const settled = calls.length;
      // The run is closed, so nothing is left reading a server behind the
      // operator's back.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
      expect(calls).toHaveLength(settled);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says what a run's status means rather than printing the table's word", async () => {
    router({
      "GET /api/servers/plex/catch-up": () =>
        json({
          ...OPEN_RUN,
          status: "interrupted",
          finished_at: "2026-09-14T09:05:00Z",
          detail: "interrupted: 124 marked, 3 done, 1 failed",
        }),
    });
    await renderCard();
    // "Catch up interrupted:" says nothing about what interrupted it, and
    // "Catch up ok:" is not a state anybody calls a finished backlog.
    expect(
      screen.getByText(/Catch up stopped when this deployment restarted:/),
    ).toBeInTheDocument();
    // The run's own summary, which was lost the moment the card was re-opened.
    expect(
      screen.getByText(/interrupted: 124 marked, 3 done, 1 failed/),
    ).toBeInTheDocument();
  });

  it("refuses a check on a configured server that has no credential anywhere", async () => {
    // The state `cardsToOpen` opens a card in, and the request is a 400 every
    // time: with nothing typed, the route resolves the stored credential and
    // finds none.
    router({});
    await renderCard({ ...PLEX, credential_source: "unset" });
    expect(screen.getByRole("button", { name: "Check Plex connection" })).toBeDisabled();
    expect(
      screen.getByText(/holds no credential for Plex, and a check needs one/),
    ).toBeInTheDocument();
    type("Plex credential", "typed");
    expect(screen.getByRole("button", { name: "Check Plex connection" })).toBeEnabled();
  });

  it("refuses a check on a card that has no address in it at all", async () => {
    // A card opened from Add a server: the body carries no url and the
    // deployment booted with none for this server, which the route refuses.
    router({}, "jellyfin");
    await renderCard(JELLYFIN);
    expect(
      screen.getByRole("button", { name: "Check Jellyfin connection" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Reload Jellyfin libraries" }),
    ).toBeDisabled();
    expect(screen.getByText(/There is no address to check/)).toBeInTheDocument();
  });

  it("does not have the page re-read after a write the server refused", async () => {
    const onChanged = vi.fn();
    router({
      "PUT /api/servers/plex": () => json({ detail: NEEDS_A_CREDENTIAL_FIRST }, 409),
    });
    await renderCard(PLEX, { onChanged });
    await click("Save Plex");
    expect(onChanged).not.toHaveBeenCalled();
  });
});
