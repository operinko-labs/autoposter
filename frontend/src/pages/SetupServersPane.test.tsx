import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SetupServersPane } from "./SetupServersPane";
import type { SetupProgress } from "../api/setup";

const NO_SERVERS = {
  plex: { configured: false, credential: false, checked: false },
  jellyfin: { configured: false, credential: false, checked: false },
};

const PROGRESS: SetupProgress = {
  password: true,
  database: true,
  database_source: "staged",
  providers: { AUTOPOSTER_TMDB_TOKEN: null },
  required: ["AUTOPOSTER_TMDB_TOKEN"],
  config: false,
  config_source: null,
  public_url: true,
  checked_systems: [],
  servers: NO_SERVERS,
};

function respond(body: unknown, status = 200): Response {
  return { ok: status < 400, status, json: async () => body } as Response;
}

function renderPane(overrides: Partial<Parameters<typeof SetupServersPane>[0]> = {}) {
  return render(
    <SetupServersPane
      busy={false}
      progress={PROGRESS}
      onSave={vi.fn(async () => true)}
      onSelect={vi.fn(async () => true)}
      {...overrides}
    />,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SetupServersPane", () => {
  it("offers one card per media server, because either one finishes the step", () => {
    renderPane();

    expect(screen.getByRole("button", { name: /^Plex/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Jellyfin/ })).toBeInTheDocument();
  });

  it("opens the Plex card on a deployment with nothing configured, and only that one", () => {
    // Review M3. The Plex token used to be a required provider key, so the
    // Plex-only operator landed on an accordion already expanded around the
    // sign-in; a first run that showed two closed cards under a disabled
    // Continue would be the one thing this step changed about that walk. Not
    // `required`, which would badge a card this deployment may never need.
    renderPane();

    expect(screen.getByTestId("accordion-body-plex")).toBeInTheDocument();
    expect(screen.queryByTestId("accordion-body-jellyfin")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in with Plex" })).toBeInTheDocument();
  });

  it("opens neither card once a server is configured, because the split decides then", () => {
    // A deployment the document already names is not a first run: what opens
    // there is the card missing its credential, and a complete server opens
    // nothing at all.
    renderPane({
      progress: {
        ...PROGRESS,
        servers: { ...NO_SERVERS, plex: { configured: true, credential: true, checked: false } },
      },
    });

    expect(screen.queryByTestId("accordion-body-plex")).toBeNull();
    expect(screen.queryByTestId("accordion-body-jellyfin")).toBeNull();
  });

  it("reads each card's Stored pill from the servers line, not from the provider map", () => {
    // The two server credentials are not in `providers` at all any more --
    // each is required exactly when ITS server is configured -- so the pill
    // that says the deployment holds one has exactly one source.
    renderPane({
      progress: {
        ...PROGRESS,
        servers: { ...NO_SERVERS, jellyfin: { configured: true, credential: true, checked: true } },
      },
    });

    expect(screen.getByTestId("held-AUTOPOSTER_JELLYFIN_APIKEY")).toHaveTextContent("Stored");
    expect(screen.getByTestId("held-AUTOPOSTER_PLEX_TOKEN")).toHaveTextContent("Not set");
    // Presence, never a value: `/progress` reports these as booleans and the
    // page must not invent a string that looks like one.
    expect(document.body.textContent).not.toContain("***REDACTED***");
  });

  it("opens the card of a server the document names but holds no credential for", () => {
    // The step gate's second clause, rendered: a `plex:` block on disk with no
    // token keeps the step unmet however complete Jellyfin is beside it, so
    // that card is the one thing on this pane the operator must act on.
    renderPane({
      progress: {
        ...PROGRESS,
        config_source: "configured",
        servers: {
          plex: { configured: true, credential: false, checked: false },
          jellyfin: { configured: true, credential: true, checked: true },
        },
      },
    });

    expect(screen.getByTestId("accordion-body-plex")).toBeInTheDocument();
    expect(screen.queryByTestId("accordion-body-jellyfin")).toBeNull();
    expect(screen.getByLabelText("AUTOPOSTER_PLEX_TOKEN")).toBeInTheDocument();
  });

  it("saves a card's credential under that server's own environment name", async () => {
    const onSave = vi.fn(async () => true);
    vi.stubGlobal("fetch", vi.fn(async () => respond({ ok: true })));
    renderPane({ onSave });
    fireEvent.click(screen.getByRole("button", { name: /^Jellyfin/ }));
    await waitFor(() => screen.getByTestId("accordion-body-jellyfin"));

    fireEvent.change(screen.getByLabelText("AUTOPOSTER_JELLYFIN_APIKEY"), {
      target: { value: "row-267-api-key" },
    });
    fireEvent.click(
      within(screen.getByTestId("accordion-body-jellyfin")).getByRole("button", { name: "Save" }),
    );

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith({ AUTOPOSTER_JELLYFIN_APIKEY: "row-267-api-key" }),
    );
  });

  it("checks a card against the check endpoint's key for that server", async () => {
    const fetchMock = vi.fn(async (path: unknown, init?: RequestInit) =>
      path === "/api/setup/check" && init?.method === "POST"
        ? respond({ ok: true, detail: "Jellyfin answered." })
        : respond({ ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPane();
    fireEvent.click(screen.getByRole("button", { name: /^Jellyfin/ }));
    await waitFor(() => screen.getByTestId("accordion-body-jellyfin"));

    fireEvent.change(screen.getByLabelText("Jellyfin address"), {
      target: { value: "https://jellyfin.invalid:8096" },
    });
    fireEvent.click(
      within(screen.getByTestId("accordion-body-jellyfin")).getByRole("button", {
        name: "Check connection",
      }),
    );

    await waitFor(() => expect(screen.getByTestId("check-result-jellyfin")).toBeInTheDocument());
    const checked = fetchMock.mock.calls.find(([path]) => path === "/api/setup/check");
    expect(JSON.parse(String(checked?.[1]?.body))).toEqual({
      system: "jellyfin",
      base_url: "https://jellyfin.invalid:8096",
      credential_value: null,
    });
  });

  it("submits the Plex card with the Plex pair alone", async () => {
    // One submit, four fields, and each card fills only its own two: a card
    // saved on its own must not name the other server's address, or the
    // document written for a Jellyfin-only deployment would carry an empty
    // `plex:` block the next boot demands a token for.
    const onSelect = vi.fn(async () => true);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: unknown) =>
        path === "/api/setup/plex/libraries"
          ? respond({ libraries: [{ key: "1", title: "Movies", type: "movie" }] })
          : respond({ ok: true }),
      ),
    );
    renderPane({ onSelect });
    // Already open: nothing is configured on this fixture, which is the first
    // run the card opens itself for.
    await waitFor(() => screen.getByTestId("accordion-body-plex"));

    fireEvent.change(screen.getByLabelText("Plex address"), {
      target: { value: "http://plex.invalid:32400" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Use this address" }));
    await waitFor(() => screen.getByLabelText("Movies"));
    fireEvent.click(screen.getByRole("button", { name: "Use this server" }));

    await waitFor(() =>
      expect(onSelect).toHaveBeenCalledWith("http://plex.invalid:32400", [], null, null),
    );
  });
});
