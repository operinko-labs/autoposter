/** The secrets accordion, which is allowed to say where a secret comes from
 * and never what it is.
 *
 * Nothing in this file puts a value anywhere a failure could print it: the one
 * typed value is asserted against the stubbed request body and nowhere else,
 * and the rest of the assertions are names, sources and sentences.
 */
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SecretsPanel } from "./SecretsPanel";

/** All four sources, the generated one, and a server credential that belongs
 * on its server's card rather than here. */
const ROWS = {
  secrets: [
    { name: "AUTOPOSTER_TMDB_TOKEN", source: "stored", generated: false },
    { name: "AUTOPOSTER_TVDB_APIKEY", source: "environment", generated: false },
    { name: "AUTOPOSTER_FANART_APIKEY", source: "state file", generated: false },
    { name: "AUTOPOSTER_RADARR_APIKEY", source: "unset", generated: false },
    { name: "AUTOPOSTER_WEBHOOK_SECRET", source: "stored", generated: true },
    { name: "AUTOPOSTER_PLEX_TOKEN", source: "stored", generated: false },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Routes by method: the listing is a GET, and a write is answered by
 * whatever the test hands in -- the two are different bodies, and a stub that
 * answered one for both would be testing itself. */
function stubSecrets(
  calls: { url: string; init?: RequestInit }[] = [],
  write: () => Response = () =>
    json({ name: "AUTOPOSTER_TMDB_TOKEN", source: "stored" }),
) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return Promise.resolve(init?.method === undefined ? json(ROWS) : write());
    }),
  );
}

async function renderPanel(onChanged?: () => void) {
  await act(async () => {
    render(<SecretsPanel onChanged={onChanged} />);
  });
  fireEvent.click(screen.getByRole("button", { name: "Secrets" }));
}

describe("SecretsPanel", () => {
  it("lists every name with its source", async () => {
    stubSecrets();
    await renderPanel();
    expect(screen.getByText("AUTOPOSTER_TMDB_TOKEN")).toBeInTheDocument();
    // Two of the listed names are stored: the token above and the generated
    // one, which is listed like any other.
    expect(screen.getAllByText("stored")).toHaveLength(2);
    expect(screen.getByText("environment")).toBeInTheDocument();
    expect(screen.getByText("state file")).toBeInTheDocument();
    expect(screen.getByText("unset")).toBeInTheDocument();
  });

  it("leaves the server credentials to their server's card", async () => {
    stubSecrets();
    await renderPanel();
    expect(screen.queryByText("AUTOPOSTER_PLEX_TOKEN")).not.toBeInTheDocument();
  });

  it("offers Replace and Clear for a stored value, Set for the rest", async () => {
    stubSecrets();
    await renderPanel();
    expect(
      screen.getByRole("button", { name: "Replace AUTOPOSTER_TMDB_TOKEN" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Clear AUTOPOSTER_TMDB_TOKEN" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Set AUTOPOSTER_RADARR_APIKEY" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear AUTOPOSTER_RADARR_APIKEY" }),
    ).not.toBeInTheDocument();
  });

  it("sends the typed value and never renders one back", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    stubSecrets(calls);
    await renderPanel();
    fireEvent.change(screen.getByLabelText("AUTOPOSTER_RADARR_APIKEY"), {
      target: { value: "a-typed-key" },
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Set AUTOPOSTER_RADARR_APIKEY" }),
      );
    });
    const write = calls.find((call) => call.init?.method === "PUT");
    expect(write?.url).toBe("/api/secrets/AUTOPOSTER_RADARR_APIKEY");
    expect(JSON.parse(String(write?.init?.body))).toEqual({ value: "a-typed-key" });
    expect(screen.queryByDisplayValue("a-typed-key")).not.toBeInTheDocument();
    expect(
      screen.getByLabelText("AUTOPOSTER_RADARR_APIKEY"),
    ).toHaveAttribute("type", "password");
  });

  it("re-reads the sources and the page's configuration after a write", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const onChanged = vi.fn();
    stubSecrets(calls);
    await renderPanel(onChanged);
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Set AUTOPOSTER_RADARR_APIKEY" }),
      );
    });
    // The listing again, because a stored value changes what every row under
    // it says about its own source.
    expect(
      calls.filter(
        (call) =>
          call.url === "/api/secrets" && (call.init?.method ?? "GET") === "GET",
      ),
    ).toHaveLength(2);
    expect(onChanged).toHaveBeenCalled();
  });

  it("says what a cleared value hands back to, and when it takes effect", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    stubSecrets(calls, () =>
      json({
        name: "AUTOPOSTER_TMDB_TOKEN",
        source: "environment",
        restart_required: true,
      }),
    );
    await renderPanel();
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Clear AUTOPOSTER_TMDB_TOKEN" }),
      );
    });
    const write = calls.find((call) => call.init?.method === "DELETE");
    expect(write?.url).toBe("/api/secrets/AUTOPOSTER_TMDB_TOKEN");
    expect(
      screen.getByText(
        "Cleared AUTOPOSTER_TMDB_TOKEN. The value that takes over is read at the next restart.",
      ),
    ).toBeInTheDocument();
  });

  it("renders the sentence the server refused with", async () => {
    stubSecrets([], () =>
      json(
        {
          detail:
            "that value cannot be stored: it is empty, too long, or carries a newline",
        },
        422,
      ),
    );
    await renderPanel();
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Set AUTOPOSTER_RADARR_APIKEY" }),
      );
    });
    expect(
      screen.getByText(
        "that value cannot be stored: it is empty, too long, or carries a newline",
      ),
    ).toBeInTheDocument();
  });

  it("points the generated secret at its own panel instead of a field", async () => {
    stubSecrets();
    await renderPanel();
    expect(screen.getByText("AUTOPOSTER_WEBHOOK_SECRET")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("AUTOPOSTER_WEBHOOK_SECRET"),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/generated by this service/)).toBeInTheDocument();
    // The rotation panel is the row's control, so it is here rather than
    // somewhere else on the tab.
    expect(
      screen.getByRole("button", { name: /rotate webhook secret/i }),
    ).toBeInTheDocument();
  });

  it("hangs the served description off the row it describes", async () => {
    stubSecrets();
    await act(async () => {
      render(
        <SecretsPanel
          descriptions={{
            "secrets.tmdb_token": "The TMDb API token metadata requests use.",
          }}
        />,
      );
    });
    fireEvent.click(screen.getByRole("button", { name: "Secrets" }));

    expect(screen.getByText("AUTOPOSTER_TMDB_TOKEN")).toHaveAttribute(
      "title",
      "The TMDb API token metadata requests use.",
    );
    // A name the served configuration described nothing for gets no empty
    // hover, the same rule the settings tree follows.
    expect(screen.getByText("AUTOPOSTER_RADARR_APIKEY")).not.toHaveAttribute(
      "title",
    );
  });

  it("keeps the whole list behind one collapsed accordion", async () => {
    stubSecrets();
    await act(async () => {
      render(<SecretsPanel />);
    });
    // Nothing is mounted until the header is pressed: the body of a closed
    // accordion is not in the document at all.
    expect(screen.queryByText("AUTOPOSTER_TMDB_TOKEN")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Secrets" }));
    const accordion = screen.getByRole("button", { name: "Secrets" }).closest(
      "section",
    );
    expect(
      within(accordion as HTMLElement).getByText("AUTOPOSTER_TMDB_TOKEN"),
    ).toBeInTheDocument();
  });
});
