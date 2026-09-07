import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { SetupProgress } from "../api/setup";
import { Setup, setupErrorMessage } from "./Setup";

// The six keys facts Amendment 6 ratifies -- config_source is the one the
// brief's original fixture predates, and it is what the wizard now reads to
// decide whether to offer step 4, never the plain `config` boolean alone.
// Typed against SetupProgress, not inferred, so a test's own override (a
// different config_source, an extra provider name) type-checks against the
// real interface rather than against this literal's narrowed shape.
const PROGRESS: SetupProgress = {
  password: true,
  database: false,
  providers: {
    AUTOPOSTER_PLEX_TOKEN: "***REDACTED***",
    AUTOPOSTER_TMDB_TOKEN: null,
  },
  required: ["AUTOPOSTER_TMDB_TOKEN"],
  config: false,
  config_source: null,
  public_url: false,
};

function respond(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    json: async () => body,
  } as Response;
}

// v2 navigation helpers: the wizard lands one pane at a time (the step
// machine's Back/Continue design, tested on its own in setupSteps.test.ts and
// in the "asks for this deployment's own address"/"goes back"/"never offers
// Back"/"skips the database step"/"keeps the typed address" cases below), so a
// test whose subject is a LATER pane must walk the earlier ones first, the
// same way an operator would.
async function submitPublicUrlStep(value = "https://autoposter.example.test") {
  await waitFor(() => screen.getByLabelText("Autoposter's own URL"));
  fireEvent.change(screen.getByLabelText("Autoposter's own URL"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}

async function submitDatabaseStep(value = "postgresql+asyncpg://u:p@db:5432/autoposter") {
  await waitFor(() => screen.getByLabelText("Database URL"));
  fireEvent.change(screen.getByLabelText("Database URL"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Test and continue" }));
}

async function goToSystemsStep() {
  await submitPublicUrlStep();
  await submitDatabaseStep();
  await waitFor(() => screen.getByTestId("systems-step"));
}

/** A stateful fetch mock for `/api/setup/progress`.
 *
 * `canNavigate` (setupSteps.ts) gates a pane on what the SERVER reports, not
 * on what a form merely submitted, so a mock that always answers the same
 * static `base` never lets the wizard past the address or database steps
 * (`farthestStep` would keep reporting them unmet). This tracks the two POSTs
 * that matter and reflects them the same way the real server would, which is
 * what lets `goToSystemsStep`/`submitPublicUrlStep`/`submitDatabaseStep` work
 * against any test's fixture. `extra` handles a test's own path, ahead of the
 * tracking, for the one or two routes a test needs to answer differently
 * (a refusal from providers or database, say). */
function progressMock(
  base: SetupProgress = PROGRESS,
  extra?: (path: string, init: RequestInit | undefined) => Response | undefined,
) {
  let publicUrlSet = base.public_url;
  let databaseSet = base.database;
  return vi.fn(async (path: unknown, init?: RequestInit) => {
    const custom = extra?.(path as string, init);
    if (custom !== undefined) return custom;
    if (path === "/api/setup/public-url" && init?.method === "POST") {
      publicUrlSet = true;
      return respond({ ok: true });
    }
    if (path === "/api/setup/database" && init?.method === "POST") {
      databaseSet = true;
      return respond({ ok: true });
    }
    return respond({ ...base, public_url: publicUrlSet, database: databaseSet });
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", progressMock());
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
    await goToSystemsStep();

    await waitFor(() =>
      expect(screen.getByTestId("held-AUTOPOSTER_PLEX_TOKEN")).toHaveTextContent("Stored"),
    );
    expect(screen.getByTestId("held-AUTOPOSTER_TMDB_TOKEN")).toHaveTextContent("Not set");
    expect(document.body.textContent).not.toContain("***REDACTED***");
  });

  it("starts every credential input empty, whatever is already stored", async () => {
    render(<Setup />);
    await goToSystemsStep();

    await waitFor(() => screen.getByLabelText("AUTOPOSTER_PLEX_TOKEN"));
    expect(screen.getByLabelText<HTMLInputElement>("AUTOPOSTER_PLEX_TOKEN").value).toBe("");
  });

  it("offers the database step once the address is set, while it is the unfinished one", async () => {
    render(<Setup />);
    await submitPublicUrlStep();

    await waitFor(() => expect(screen.getByLabelText("Database URL")).toBeInTheDocument());
  });

  it("does not offer to finish while a required credential is missing", async () => {
    render(<Setup />);
    await goToSystemsStep();

    expect(screen.queryByRole("button", { name: "Start autoposter" })).toBeNull();
  });

  it("offers to finish once every step reports done", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond({
          ...PROGRESS,
          database: true,
          required: [],
          config: true,
          config_source: "state",
          public_url: true,
        }),
      ),
    );

    render(<Setup />);
    await submitPublicUrlStep();
    await waitFor(() => screen.getByTestId("systems-step"));
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Start autoposter" })).toBeInTheDocument(),
    );
  });

  it("does not offer the configuration step once a document already resolves", async () => {
    // Amendment 6: config_source, not the plain config flag, is what decides
    // whether the wizard offers step 4 -- a deployment whose document is
    // already mounted (a ConfigMap, compose's bind-mounted example) is never
    // asked for a Plex URL a second time. The "and says so" pill v1 rendered
    // here is systems-step UI Tasks 2/3 replace outright, so this task's
    // scaffold is left checked only on the part it still owns: the field does
    // not appear.
    vi.stubGlobal("fetch", progressMock({ ...PROGRESS, config: true, config_source: "configured" }));

    render(<Setup />);
    await goToSystemsStep();

    expect(screen.queryByLabelText("Plex server URL")).toBeNull();
  });

  it("shows a generated webhook secret once, with a copy control and a warning it will not return", async () => {
    // v2 moves the reveal to the finish pane (facts: "finish: the generated
    // secret shown once, registration results, what was skipped"), so this
    // mock reports everything else satisfied once the provider step is
    // submitted, and the test walks to finish to see it -- same secret,
    // same one-time guarantee, later pane.
    let providersSubmitted = false;
    const fetchMock = vi.fn(async (path: unknown, init?: RequestInit) => {
      if (path === "/api/setup/providers" && init?.method === "POST") {
        providersSubmitted = true;
        return respond({
          providers: { ...PROGRESS.providers, AUTOPOSTER_TMDB_TOKEN: "***REDACTED***" },
          webhook_secret: "row-121-generated-secret",
        });
      }
      if (providersSubmitted) {
        return respond({
          ...PROGRESS,
          database: true,
          required: [],
          config: true,
          config_source: "state",
          public_url: true,
        });
      }
      return respond({ ...PROGRESS, database: true, public_url: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await submitPublicUrlStep();
    await waitFor(() => screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"));

    fireEvent.change(screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"), {
      target: { value: "pasted-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

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
      progressMock({
        ...PROGRESS,
        providers: { ...PROGRESS.providers, AUTOPOSTER_WEBHOOK_SECRET: null },
        required: [...PROGRESS.required, "AUTOPOSTER_WEBHOOK_SECRET"],
      }),
    );

    render(<Setup />);
    await goToSystemsStep();

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
      vi.stubGlobal(
        "fetch",
        progressMock(PROGRESS, (path, init) =>
          path === "/api/setup/providers" && init?.method === "POST"
            ? respond({ detail }, status)
            : undefined,
        ),
      );

      render(<Setup />);
      await goToSystemsStep();
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

  it("keeps a typed database URL on a 400 (the database step's designed-for refusal)", async () => {
    // /api/setup/database answers 400 by connecting and failing -- the single
    // most likely outcome of this step, and the one Task 4 round 2 found
    // wiped the whole connection string on every refusal.
    vi.stubGlobal(
      "fetch",
      progressMock(PROGRESS, (path, init) =>
        path === "/api/setup/database" && init?.method === "POST"
          ? respond({ detail: "the database did not answer (OperationalError)" }, 400)
          : undefined,
      ),
    );

    render(<Setup />);
    await submitPublicUrlStep();
    await waitFor(() => screen.getByLabelText("Database URL"));

    fireEvent.change(screen.getByLabelText("Database URL"), {
      target: { value: "postgresql+asyncpg://u:p@bad-host:5432/autoposter" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Test and continue" }));

    await waitFor(() => screen.getByText("the database did not answer (OperationalError)"));
    expect(screen.getByLabelText<HTMLInputElement>("Database URL").value).toBe(
      "postgresql+asyncpg://u:p@bad-host:5432/autoposter",
    );
  });

  it("clears the master password field on a wrong-password refusal regardless", async () => {
    // The one OneFieldPane caller that keeps clearing unconditionally: a
    // wrong-password 401 emptying the field is correct behaviour, not a paste
    // the operator has to reconstruct.
    const fetchMock = vi.fn(async (path: unknown) => {
      if (path === "/api/setup/state") return respond({ setup: true, password_set: true });
      if (path === "/api/setup/password") return respond({ detail: "invalid credentials" }, 401);
      return respond({ detail: "not authenticated" }, 401);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await waitFor(() => screen.getByLabelText("Prove the master password"));

    fireEvent.change(screen.getByLabelText("Prove the master password"), {
      target: { value: "wrong-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => screen.getByText("invalid credentials"));
    expect(screen.getByLabelText<HTMLInputElement>("Prove the master password").value).toBe("");
  });

  describe("the webhook secret's Copy control", () => {
    async function renderWithSecret() {
      // Same v2 finish-pane reveal as the standalone webhook-secret test
      // above: everything else reports satisfied once providers are
      // submitted, and the test walks to finish to see the secret.
      let providersSubmitted = false;
      const fetchMock = vi.fn(async (path: unknown, init?: RequestInit) => {
        if (path === "/api/setup/providers" && init?.method === "POST") {
          providersSubmitted = true;
          return respond({
            providers: { ...PROGRESS.providers, AUTOPOSTER_TMDB_TOKEN: "***REDACTED***" },
            webhook_secret: "row-121-generated-secret",
          });
        }
        if (providersSubmitted) {
          return respond({
            ...PROGRESS,
            database: true,
            required: [],
            config: true,
            config_source: "state",
            public_url: true,
          });
        }
        return respond({ ...PROGRESS, database: true, public_url: true });
      });
      vi.stubGlobal("fetch", fetchMock);

      render(<Setup />);
      await submitPublicUrlStep();
      await waitFor(() => screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"));
      fireEvent.change(screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"), {
        target: { value: "pasted-value" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Save and continue" }));
      await waitFor(() => expect(screen.getByRole("button", { name: "Continue" })).not.toBeDisabled());
      fireEvent.click(screen.getByRole("button", { name: "Continue" }));
      await waitFor(() => screen.getByTestId("webhook-secret-value"));
    }

    it("says Copied when the Clipboard API succeeds", async () => {
      const writeText = vi.fn(async () => undefined);
      vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

      await renderWithSecret();
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));

      await waitFor(() => expect(screen.getByTestId("webhook-copy-status")).toHaveTextContent("Copied"));
      expect(writeText).toHaveBeenCalledWith("row-121-generated-secret");
    });

    it("never clicks into silence when navigator.clipboard is unavailable (plain-HTTP compose)", async () => {
      vi.stubGlobal("navigator", { ...navigator, clipboard: undefined });

      await renderWithSecret();
      fireEvent.click(screen.getByRole("button", { name: "Copy" }));

      await waitFor(() =>
        expect(screen.getByTestId("webhook-copy-status")).toHaveTextContent(
          "Select and copy the value above.",
        ),
      );
    });
  });

  it("asks for this deployment's own address before the database", async () => {
    render(<Setup />);

    await waitFor(() => expect(screen.getByLabelText("Autoposter's own URL")).toBeInTheDocument());
    expect(screen.queryByLabelText("Database URL")).toBeNull();
  });

  it("goes back to a finished step without asking the server anything", async () => {
    // Recon section 6.2: forward is gated by the server, back is free and
    // purely client-side -- there is no unstage endpoint, so there is nothing
    // to get wrong.
    const fetchMock = vi.fn(async (path: unknown) => {
      if (path === "/api/setup/public-url") return respond({ ok: true });
      return respond({ ...PROGRESS, public_url: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await waitFor(() => screen.getByLabelText("Autoposter's own URL"));
    fireEvent.change(screen.getByLabelText("Autoposter's own URL"), {
      target: { value: "https://autoposter.example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => screen.getByLabelText("Database URL"));

    const before = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    await waitFor(() => screen.getByLabelText("Autoposter's own URL"));
    expect(fetchMock.mock.calls.length).toBe(before);
  });

  it("never offers Back on the first step", async () => {
    const fetchMock = vi.fn(async (path: unknown) => {
      if (path === "/api/setup/state") return respond({ setup: true, password_set: false });
      return respond({ detail: "not authenticated" }, 401);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);

    await waitFor(() => screen.getByLabelText("Set the master password"));
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
  });

  it("skips the database step when the environment already resolved one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: unknown) => {
        if (path === "/api/setup/public-url") return respond({ ok: true });
        return respond({ ...PROGRESS, database: true, public_url: true });
      }),
    );

    render(<Setup />);
    await waitFor(() => screen.getByLabelText("Autoposter's own URL"));
    fireEvent.change(screen.getByLabelText("Autoposter's own URL"), {
      target: { value: "https://autoposter.example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(screen.getByTestId("systems-step")).toBeInTheDocument());
    expect(screen.queryByLabelText("Database URL")).toBeNull();
  });

  it("keeps the typed address when the server refuses it, and shows the fixed sentence", async () => {
    const detail =
      "this must be an http:// or https:// address with a host, and with no username or password in it";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: unknown, init?: RequestInit) => {
        if (path === "/api/setup/public-url" && init?.method === "POST") {
          return respond({ detail }, 400);
        }
        return respond(PROGRESS);
      }),
    );

    render(<Setup />);
    await waitFor(() => screen.getByLabelText("Autoposter's own URL"));
    fireEvent.change(screen.getByLabelText("Autoposter's own URL"), {
      target: { value: "autoposter.example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => screen.getByText(detail));
    expect(screen.getByLabelText<HTMLInputElement>("Autoposter's own URL").value).toBe(
      "autoposter.example.test",
    );
  });
});
