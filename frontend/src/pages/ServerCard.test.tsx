/** One server's card. A form, because a server is a unit: address, credential
 * and exclusions belong together.
 *
 * Nothing here puts a typed credential anywhere a failure could print it: the
 * one typed value is asserted against the stubbed request body and nowhere
 * else, and everything else asserted is a name, a state or a sentence.
 *
 * The stub routes by URL AND method. A card reaches eight routes, several of
 * them at one address with different verbs (the catch-up trio) and two of them
 * at the same verb on different addresses (the two PUTs), so a stub that
 * answered one body for every request would be asserting against itself.
 * Anything a test has not declared answers a 500 that names it, which is how a
 * request the card should not have made shows up as a failure rather than as
 * a passing test.
 */
import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ServerCard } from "./ServerCard";
import type { ServerRow } from "../api/servers";

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

/** The sentence `api/servers.py` refuses a first save with, verbatim. */
const NEEDS_A_CREDENTIAL_FIRST =
  "set this server's credential before saving it; a configured server " +
  "without one sends the deployment back to first-start setup";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface Call {
  url: string;
  method: string;
  /** The parsed body, or undefined when the request carried none. A DELETE
   * with no body at all is a 422 from the request validator, so the
   * distinction between "no body" and "{}" is one these tests assert. */
  body: unknown;
  sentBody: boolean;
}

type Route = (init: RequestInit | undefined) => Response;

/** Stub `fetch` with one handler per `"METHOD /path"`, and record every call.
 *
 * The catch-up read is declared here rather than in every test because a
 * configured card makes it when it opens; a test that cares about it says so
 * by declaring its own. */
function router(routes: Record<string, Route>): Call[] {
  const calls: Call[] = [];
  const table: Record<string, Route> = {
    "GET /api/servers/plex/catch-up": () => json({ run: null }),
    ...routes,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const sentBody = init?.body !== undefined && init?.body !== null;
      calls.push({
        url,
        method,
        body: sentBody ? JSON.parse(String(init?.body)) : undefined,
        sentBody,
      });
      const route = table[`${method} ${url}`];
      return Promise.resolve(
        route === undefined
          ? json({ detail: `the card asked for ${method} ${url}` }, 500)
          : route(init),
      );
    }),
  );
  return calls;
}

async function renderCard(
  server: ServerRow = PLEX,
  revision: string | null = "r1",
  onChanged: () => void = () => {},
) {
  await act(async () => {
    render(<ServerCard server={server} revision={revision} onChanged={onChanged} />);
  });
}

async function click(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function bodyOf(calls: Call[], method: string, url: string): unknown {
  const found = calls.find((call) => call.method === method && call.url === url);
  if (found === undefined) throw new Error(`no ${method} ${url} was sent`);
  return found.body;
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
    await click("Check connection");
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
    await click("Check connection");
    expect(bodyOf(calls, "POST", "/api/servers/plex/check")).toEqual({});
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
    await click("Check connection");
    expect(screen.getByText("connected, version 1.41.0.8992")).toBeInTheDocument();
  });

  it("reloads the libraries and ticks the ones that are not excluded", async () => {
    router({ "POST /api/servers/plex/libraries": () => json(LIBRARIES) });
    await renderCard();
    await click("Reload libraries");
    expect(screen.getByLabelText("Movies")).toBeChecked();
    expect(screen.getByLabelText("Photos")).not.toBeChecked();
  });

  it("shows the server's own sentence when the library read is refused", async () => {
    router({
      "POST /api/servers/plex/libraries": () =>
        json({ detail: "Plex refused the credential." }, 502),
    });
    await renderCard();
    await click("Reload libraries");
    expect(screen.getByText("Plex refused the credential.")).toBeInTheDocument();
  });

  it("saves the address, the complement of the tick-list, and the switches", async () => {
    const calls = router({
      "POST /api/servers/plex/libraries": () => json(LIBRARIES),
      "PUT /api/servers/plex": () => json(SAVED),
    });
    await renderCard();
    await click("Reload libraries");
    fireEvent.click(screen.getByLabelText("Movies"));
    await click("Save");
    expect(bodyOf(calls, "PUT", "/api/servers/plex")).toEqual({
      url: "http://plex:32400",
      // The stored exclusion first, then the one just unticked: the list is
      // seeded from what is stored and appended to, so a library the server
      // has stopped listing keeps its exclusion.
      excluded_libraries: ["Photos", "Movies"],
      switches: {},
      expected_revision: "r1",
      confirm: false,
    });
  });

  it("sends only the switches that were set, as a delta", async () => {
    const calls = router({ "PUT /api/servers/plex": () => json(SAVED) });
    await renderCard();
    type("Upload badged artwork to Plex", "on");
    type("Write metadata to Plex", "off");
    await click("Save");
    expect(
      (bodyOf(calls, "PUT", "/api/servers/plex") as { switches: unknown }).switches,
    ).toEqual({
      "badges.upload_to_plex": true,
      "operations.write_to_plex": false,
    });
  });

  it("says the saved address is not the one a check reaches until a restart", async () => {
    router({ "PUT /api/servers/plex": () => json(SAVED) });
    await renderCard();
    await click("Save");
    expect(
      screen.getByText(/connection check reaches the saved address only once/),
    ).toBeInTheDocument();
  });

  it("offers no save at all when the page was served no revision", async () => {
    // Both writes require one, so a card that sent the body anyway would be
    // trading a disabled button for a guaranteed refusal.
    router({});
    await renderCard(PLEX, null);
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Remove server" })).toBeDisabled();
  });

  it("puts the credential field first on a server that is not configured", async () => {
    router({});
    await renderCard(JELLYFIN);
    const credential = screen.getByLabelText("Jellyfin credential");
    const address = screen.getByLabelText("Jellyfin address");
    expect(
      credential.compareDocumentPosition(address) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("renders the server's refusal to save a server that has no credential", async () => {
    router({
      "PUT /api/servers/jellyfin": () => json({ detail: NEEDS_A_CREDENTIAL_FIRST }, 409),
    });
    await renderCard(JELLYFIN);
    type("Jellyfin address", "http://jf:8096");
    await click("Save");
    expect(screen.getByText(NEEDS_A_CREDENTIAL_FIRST)).toBeInTheDocument();
  });

  it("stores a typed credential through its own route and clears the field", async () => {
    const calls = router({
      "PUT /api/servers/plex/credential": () =>
        json({ name: "plex", credential_source: "stored" }),
    });
    await renderCard();
    type("Plex credential", "a-token");
    await click("Save credential");
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.url)).toEqual([
      "/api/servers/plex/credential",
    ]);
    expect(screen.getByLabelText("Plex credential")).toHaveValue("");
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
        json(
          reads++ === 0
            ? { run: null }
            : {
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
              },
        ),
      "POST /api/servers/plex/catch-up": () =>
        json({ run_id: 7, server: "plex", cadence_seconds: 60 }),
    });
    await renderCard();
    expect(screen.getByText("No catch-up has run for Plex yet.")).toBeInTheDocument();
    await click("Catch up");
    expect(
      calls.filter((call) => call.method === "POST").map((call) => call.url),
    ).toEqual(["/api/servers/plex/catch-up"]);
    expect(screen.getByText(/120 due/)).toBeInTheDocument();
    // A run that is still open is one that can be called off.
    expect(screen.getByRole("button", { name: "Cancel catch-up" })).toBeInTheDocument();
  });

  it("retries this server's failed rows through its own route", async () => {
    const calls = router({
      "POST /api/servers/plex/retry-failed": () =>
        json({ server: "plex", artwork: 4, metadata: 2 }),
    });
    await renderCard();
    await click("Retry failed");
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
    fireEvent.click(screen.getByRole("button", { name: "Remove server" }));
    await click("Yes, remove Plex");
    const removal = calls.find((call) => call.method === "DELETE");
    // A bodyless DELETE is refused by the request validator before the
    // handler runs, so the body is sent even when both fields are defaults.
    expect(removal?.sentBody).toBe(true);
    expect(removal?.body).toEqual({ expected_revision: "r1", confirm: true });
  });

  it("says the credential survived a removal that could not clear it", async () => {
    router({
      "DELETE /api/servers/plex": () => json({ ...SAVED, credential_cleared: false }),
    });
    await renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Remove server" }));
    await click("Yes, remove Plex");
    expect(
      screen.getByText(/its credential could not be cleared and is still stored/),
    ).toBeInTheDocument();
  });

  it("shows the server's refusal to be removed rather than retrying", async () => {
    const refusal =
      "this is the only configured media server; configure another one before removing it";
    router({ "DELETE /api/servers/plex": () => json({ detail: refusal }, 409) });
    await renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Remove server" }));
    await click("Yes, remove Plex");
    expect(screen.getByText(refusal)).toBeInTheDocument();
  });
});
