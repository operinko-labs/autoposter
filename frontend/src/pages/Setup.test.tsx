import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import { Setup, setupErrorMessage } from "./Setup";

// The six keys facts Amendment 6 ratifies -- config_source is the one the
// brief's original fixture predates, and it is what the wizard now reads to
// decide whether to offer step 4, never the plain `config` boolean alone.
const PROGRESS = {
  password: true,
  database: false,
  providers: {
    AUTOPOSTER_PLEX_TOKEN: "***REDACTED***",
    AUTOPOSTER_TMDB_TOKEN: null,
  },
  required: ["AUTOPOSTER_TMDB_TOKEN"],
  config: false,
  config_source: null,
};

function respond(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    json: async () => body,
  } as Response;
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => respond(PROGRESS)));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("setupErrorMessage", () => {
  it("shows the server's fixed sentence verbatim, because it is fixed", () => {
    // Every refusal this API makes is a constant or a class name (row 213),
    // which is exactly what makes it safe to render.
    const message = setupErrorMessage(
      new ApiError(400, "the database did not answer (OperationalError)",
        "the database did not answer (OperationalError)"),
    );
    expect(message).toBe("the database did not answer (OperationalError)");
  });

  it("does not blame the input when the request never arrived", () => {
    const message = setupErrorMessage(new TypeError("Failed to fetch"));
    expect(message).toContain("Could not reach the server");
  });
});

describe("Setup", () => {
  it("renders the credential the deployment holds as a state, never as a value", async () => {
    render(<Setup />);

    await waitFor(() =>
      expect(screen.getByTestId("held-AUTOPOSTER_PLEX_TOKEN")).toHaveTextContent("Stored"),
    );
    expect(screen.getByTestId("held-AUTOPOSTER_TMDB_TOKEN")).toHaveTextContent("Not set");
    expect(document.body.textContent).not.toContain("***REDACTED***");
  });

  it("starts every credential input empty, whatever is already stored", async () => {
    render(<Setup />);

    await waitFor(() => screen.getByLabelText("AUTOPOSTER_PLEX_TOKEN"));
    expect(screen.getByLabelText<HTMLInputElement>("AUTOPOSTER_PLEX_TOKEN").value).toBe("");
  });

  it("offers the database step first while it is the unfinished one", async () => {
    render(<Setup />);

    await waitFor(() => expect(screen.getByLabelText("Database URL")).toBeInTheDocument());
  });

  it("does not offer to finish while a required credential is missing", async () => {
    render(<Setup />);

    await waitFor(() => screen.getByLabelText("Database URL"));
    expect(screen.queryByRole("button", { name: "Start autoposter" })).toBeNull();
  });

  it("offers to finish once every step reports done", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond({ ...PROGRESS, database: true, required: [], config: true, config_source: "state" }),
      ),
    );

    render(<Setup />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Start autoposter" })).toBeInTheDocument(),
    );
  });

  it("does not offer the configuration step once a document already resolves, and says so", async () => {
    // Amendment 6: config_source, not the plain config flag, is what decides
    // whether the wizard offers step 4 -- a deployment whose document is
    // already mounted (a ConfigMap, compose's bind-mounted example) is told
    // that plainly rather than asked for a Plex URL a second time.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond({ ...PROGRESS, config: true, config_source: "configured" })),
    );

    render(<Setup />);

    await waitFor(() => screen.getByTestId("config-satisfied"));
    expect(screen.queryByLabelText("Plex server URL")).toBeNull();
    expect(screen.getByTestId("config-satisfied")).toHaveTextContent("configured");
  });

  it("shows a generated webhook secret once, with a copy control and a warning it will not return", async () => {
    const fetchMock = vi.fn(async (path: unknown, init?: RequestInit) => {
      if (path === "/api/setup/providers" && init?.method === "POST") {
        return respond({
          providers: { ...PROGRESS.providers, AUTOPOSTER_TMDB_TOKEN: "***REDACTED***" },
          webhook_secret: "row-121-generated-secret",
        });
      }
      return respond(PROGRESS);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await waitFor(() => screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"));

    fireEvent.change(screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"), {
      target: { value: "pasted-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));

    await waitFor(() =>
      expect(screen.getByTestId("webhook-secret-value")).toHaveTextContent(
        "row-121-generated-secret",
      ),
    );
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByText(/will not be shown again/i)).toBeInTheDocument();
  });

  it("asks to set the master password, with length guidance, on a genuinely first visit", async () => {
    // No hash persisted yet: /api/setup/state answers password_set: false,
    // and there is nothing to prove -- the reload note would be meaningless
    // here, since nothing has been set to lose.
    const fetchMock = vi.fn(async (path: unknown) => {
      if (path === "/api/setup/state") return respond({ setup: true, password_set: false });
      return respond({ detail: "not authenticated" }, 401);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);

    await waitFor(() => screen.getByLabelText("Set the master password"));
    expect(screen.getByText(/at least 12 characters/i)).toBeInTheDocument();
    expect(screen.queryByTestId("password-reload-note")).toBeNull();
  });

  it("tells the operator that a reload loses an unfinished wizard's progress", async () => {
    // A hash is already persisted (password_set: true) -- the reload path --
    // but no token survives a reload (it lives only in memory), so the
    // progress fetch 401s and the password pane returns, now asking to PROVE
    // the password rather than to set one, with the reload note attached.
    const fetchMock = vi.fn(async (path: unknown) => {
      if (path === "/api/setup/state") return respond({ setup: true, password_set: true });
      return respond({ detail: "not authenticated" }, 401);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);

    await waitFor(() => screen.getByLabelText("Prove the master password"));
    expect(screen.queryByText(/at least 12 characters/i)).toBeNull();
    expect(screen.getByTestId("password-reload-note")).toHaveTextContent(/reload/i);
  });

  it("renders AUTOPOSTER_WEBHOOK_SECRET as a status, not a field, because the server refuses to accept one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond({
          ...PROGRESS,
          providers: { ...PROGRESS.providers, AUTOPOSTER_WEBHOOK_SECRET: null },
          required: [...PROGRESS.required, "AUTOPOSTER_WEBHOOK_SECRET"],
        }),
      ),
    );

    render(<Setup />);

    await waitFor(() => screen.getByTestId("held-AUTOPOSTER_WEBHOOK_SECRET"));
    expect(screen.getByTestId("held-AUTOPOSTER_WEBHOOK_SECRET")).toHaveTextContent("Not set");
    expect(screen.queryByLabelText("AUTOPOSTER_WEBHOOK_SECRET")).toBeNull();
    expect(screen.getByText(/generated for you when you save/i)).toBeInTheDocument();
  });

  it.each([
    [429, "too many attempts"],
    [503, "the state directory could not be written"],
  ] as const)(
    "keeps pasted provider values after a %i refusal, and shows the server's sentence",
    async (status, detail) => {
      const fetchMock = vi.fn(async (path: unknown, init?: RequestInit) => {
        if (path === "/api/setup/providers" && init?.method === "POST") {
          return respond({ detail }, status);
        }
        return respond(PROGRESS);
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<Setup />);
      await waitFor(() => screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"));

      fireEvent.change(screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"), {
        target: { value: "pasted-value" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));

      await waitFor(() => screen.getByText(detail));
      expect(screen.getByLabelText<HTMLInputElement>("AUTOPOSTER_TMDB_TOKEN").value).toBe(
        "pasted-value",
      );
    },
  );
});
