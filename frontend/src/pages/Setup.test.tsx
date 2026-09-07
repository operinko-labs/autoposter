import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { SetupProgress } from "../api/setup";
import { Setup, setupErrorMessage } from "./Setup";

// The keys facts Amendment 6 ratifies -- config_source is the one the brief's
// original fixture predates, and it is what the wizard now reads to decide
// whether to offer step 4, never the plain `config` boolean alone. Its pair
// `database_source` says the same thing for the database step: the boolean
// says the step is MET, the word says whether the operator may still change
// the answer.
// Typed against SetupProgress, not inferred, so a test's own override (a
// different config_source, an extra provider name) type-checks against the
// real interface rather than against this literal's narrowed shape.
const PROGRESS: SetupProgress = {
  password: true,
  database: false,
  database_source: "missing",
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
// same way an operator would -- starting at the password, which is what mints
// the token every later call carries.
async function submitPasswordStep(value = "row-121-master-passphrase") {
  await waitFor(() => screen.getByLabelText("Set the master password"));
  fireEvent.change(screen.getByLabelText("Set the master password"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}

async function goToUrlStep() {
  await submitPasswordStep();
  await waitFor(() => screen.getByLabelText("Autoposter's own URL"));
}

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
  await goToUrlStep();
  await submitPublicUrlStep();
  await submitDatabaseStep();
  await waitFor(() => screen.getByTestId("systems-step"));
}

/** The wizard walked to its last pane, with a secret waiting on the server's
 * once-only route.
 *
 * The providers save answers with the presence map ALONE -- the value it
 * minted is staged server-side -- and `/api/setup/webhook-secret` serves it
 * the first time it is asked for and `null` afterwards, which is the route's
 * own contract mirrored here: a page that asked twice would show nothing the
 * second time and fail these tests. */
function secretMock() {
  let authenticated = false;
  let providersSubmitted = false;
  let served = false;
  return vi.fn(async (path: unknown, init?: RequestInit) => {
    if (path === "/api/setup/state") return respond({ setup: true, password_set: false });
    if (path === "/api/setup/password" && init?.method === "POST") {
      authenticated = true;
      return respond({ token: "row-121-setup-token" });
    }
    if (path === "/api/setup/public-url" && init?.method === "POST") {
      return respond({ ok: true });
    }
    if (path === "/api/setup/providers" && init?.method === "POST") {
      providersSubmitted = true;
      return respond({
        providers: { ...PROGRESS.providers, AUTOPOSTER_TMDB_TOKEN: "***REDACTED***" },
      });
    }
    if (path === "/api/setup/webhook-secret") {
      if (served) return respond({ webhook_secret: null });
      served = true;
      return respond({ webhook_secret: "row-121-generated-secret" });
    }
    if (!authenticated) return respond({ detail: "not authenticated" }, 401);
    if (providersSubmitted) {
      return respond({
        ...PROGRESS,
        database: true,
        database_source: "resolved",
        required: [],
        config: true,
        config_source: "state",
        public_url: true,
      });
    }
    return respond({ ...PROGRESS, database: true, database_source: "resolved", public_url: true });
  });
}

async function goToFinishStepThroughProviders() {
  await goToUrlStep();
  await submitPublicUrlStep();
  await waitFor(() => screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"));
  fireEvent.change(screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"), {
    target: { value: "pasted-value" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Continue" })).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
}

/** A stateful fetch mock for `/api/setup/progress`.
 *
 * `canNavigate` (setupSteps.ts) gates a pane on what the SERVER reports, not
 * on what a form merely submitted, so a mock that always answers the same
 * static `base` never lets the wizard past the address or database steps
 * (`farthestStep` would keep reporting them unmet). This tracks the three
 * POSTs that matter and reflects them the same way the real server would,
 * which is what lets `goToSystemsStep`/`submitPublicUrlStep`/
 * `submitDatabaseStep` work against any test's fixture. `extra` handles a
 * test's own path, ahead of the tracking, for the one or two routes a test
 * needs to answer differently (a refusal from providers or database, say).
 *
 * `/progress` answers 401 until the password has been posted, because that is
 * when the server mints the token -- a mock that answers it unauthenticated
 * would let a page skip step 1 in a way no deployment does. */
function progressMock(
  base: SetupProgress = PROGRESS,
  extra?: (path: string, init: RequestInit | undefined) => Response | undefined,
) {
  let authenticated = false;
  let publicUrlSet = base.public_url;
  let databaseSet = base.database;
  // The server's own derivation: a URL the wizard just staged reports
  // `staged`, which is what keeps the step in the order and reachable by Back.
  let databaseSource = base.database_source;
  return vi.fn(async (path: unknown, init?: RequestInit) => {
    const custom = extra?.(path as string, init);
    if (custom !== undefined) return custom;
    if (path === "/api/setup/state") return respond({ setup: true, password_set: false });
    if (path === "/api/setup/password" && init?.method === "POST") {
      authenticated = true;
      return respond({ token: "row-121-setup-token" });
    }
    if (path === "/api/setup/public-url" && init?.method === "POST") {
      publicUrlSet = true;
      return respond({ ok: true });
    }
    if (path === "/api/setup/database" && init?.method === "POST") {
      databaseSet = true;
      databaseSource = "staged";
      return respond({ ok: true });
    }
    if (path === "/api/setup/webhook-secret") return respond({ webhook_secret: null });
    if (!authenticated) return respond({ detail: "not authenticated" }, 401);
    return respond({
      ...base,
      public_url: publicUrlSet,
      database: databaseSet,
      database_source: databaseSource,
    });
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
    // Through the real page and not the accordion alone: the pills are the
    // component's, the split between held and missing is the server's, and
    // this is the one test that walks the wiring between them.
    render(<Setup />);
    await goToSystemsStep();

    await waitFor(() =>
      expect(screen.getByTestId("held-AUTOPOSTER_PLEX_TOKEN")).toHaveTextContent("Stored"),
    );
    expect(screen.getByTestId("held-AUTOPOSTER_TMDB_TOKEN")).toHaveTextContent("Not set");
    expect(document.body.textContent).not.toContain("***REDACTED***");
    // Facts C8: the held one is folded away, the required-and-missing one is
    // open, and neither state was asked of or sent to the server.
    expect(screen.queryByTestId("accordion-body-plex")).toBeNull();
    expect(screen.getByTestId("accordion-body-tmdb")).toBeInTheDocument();
  });

  it("offers the database step once the address is set, while it is the unfinished one", async () => {
    render(<Setup />);
    await goToUrlStep();
    await submitPublicUrlStep();

    await waitFor(() => expect(screen.getByLabelText("Database URL")).toBeInTheDocument());
  });

  it("does not offer to finish while a required credential is missing", async () => {
    // The live gate, not a button that lives on another pane: Continue is what
    // leaves the systems pane, and it stays disabled until the server says
    // every step is done (`farthestStep(progress) === "finish"`). Asserting
    // instead that "Start autoposter" is absent passes merely by being on a
    // different pane -- the helper-green/wired-path-different shape that has
    // produced two same-branch defects already.
    render(<Setup />);
    await goToSystemsStep();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled(),
    );
    expect(PROGRESS.required.length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Start autoposter" })).toBeNull();
  });

  it("offers to finish once every step reports done", async () => {
    vi.stubGlobal(
      "fetch",
      progressMock({
        ...PROGRESS,
        database: true,
        database_source: "resolved",
        required: [],
        config: true,
        config_source: "state",
        public_url: true,
      }),
    );

    render(<Setup />);
    await goToUrlStep();
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
    // secret shown once, registration results, what was skipped") and off the
    // providers response with it: the save MINTS the secret and answers with
    // the presence map alone, and the pane that shows it asks the server's
    // once-only route for it when it mounts. A reload between the two
    // therefore costs nothing.
    const fetchMock = secretMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await goToFinishStepThroughProviders();

    await waitFor(() =>
      expect(screen.getByTestId("webhook-secret-value")).toHaveTextContent(
        "row-121-generated-secret",
      ),
    );
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
    expect(screen.getByText(/will not be shown again/i)).toBeInTheDocument();
    // The value never came from the save that minted it.
    const saved = fetchMock.mock.calls.filter(([path]) => path === "/api/setup/providers");
    expect(saved.length).toBe(1);
  });

  it("asks the once-only route for the secret only on the pane that shows it", async () => {
    const fetchMock = secretMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await goToUrlStep();
    await submitPublicUrlStep();
    await waitFor(() => screen.getByLabelText("AUTOPOSTER_TMDB_TOKEN"));

    // Still on the systems pane: nothing has asked for the value yet, so a
    // wizard abandoned here has not spent the one serve.
    expect(
      fetchMock.mock.calls.filter(([path]) => path === "/api/setup/webhook-secret").length,
    ).toBe(0);
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
    // It is minted by the first save of ANY system, which is the one thing an
    // operator on a deployment whose other credentials are all already stored
    // would otherwise have no way to guess.
    expect(screen.getByText(/generated for you when you save any system/i)).toBeInTheDocument();
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
      fireEvent.click(screen.getByRole("button", { name: "Save" }));

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
    await goToUrlStep();
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
      // above: the save mints, the finish pane asks the once-only route.
      vi.stubGlobal("fetch", secretMock());

      render(<Setup />);
      await goToFinishStepThroughProviders();
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
    await goToUrlStep();

    await waitFor(() => expect(screen.getByLabelText("Autoposter's own URL")).toBeInTheDocument());
    expect(screen.queryByLabelText("Database URL")).toBeNull();
  });

  it("goes back to a finished step without asking the server anything", async () => {
    // Recon section 6.2: forward is gated by the server, back is free and
    // purely client-side -- there is no unstage endpoint, so there is nothing
    // to get wrong.
    const fetchMock = progressMock();
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await goToUrlStep();
    await submitPublicUrlStep();
    await waitFor(() => screen.getByLabelText("Database URL"));

    const before = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    await waitFor(() => screen.getByLabelText("Autoposter's own URL"));
    expect(fetchMock.mock.calls.length).toBe(before);
  });

  it("shows the address the server holds as Stored, and takes an empty submit as keeping it", async () => {
    // Facts C7 for the panes this task owns: the value is never sent back to
    // the page, so a step navigated back into shows an empty field -- and
    // without a pill and an enabled Continue, the only way forward from it is
    // to re-type an address the server already has.
    vi.stubGlobal("fetch", progressMock());

    render(<Setup />);
    await goToUrlStep();
    expect(screen.getByTestId("stored-setup-public-url")).toHaveTextContent("Not set");

    await submitPublicUrlStep();
    await waitFor(() => screen.getByLabelText("Database URL"));
    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    await waitFor(() => screen.getByLabelText("Autoposter's own URL"));
    expect(screen.getByTestId("stored-setup-public-url")).toHaveTextContent("Stored");
    expect(screen.getByLabelText<HTMLInputElement>("Autoposter's own URL").value).toBe("");
    expect(screen.getByRole("button", { name: "Continue" })).not.toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(screen.getByLabelText("Database URL")).toBeInTheDocument());
  });

  it("does not fall back to step 1 when the mount probe answers after the password did", async () => {
    // The mount /progress fetch carries no token -- it is issued before the
    // password mints one -- so it 401s. If its rejection cleared the progress
    // the accepted submit had just fetched, the wizard would bounce back to
    // the password pane; with the advance keyed on a one-shot ref it would
    // then never advance again for the life of the tab.
    let releaseMountProbe: () => void = () => undefined;
    const mountProbe = new Promise<void>((resolve) => {
      releaseMountProbe = resolve;
    });
    let progressCalls = 0;
    let publicUrlSet = false;
    const fetchMock = vi.fn(async (path: unknown, init?: RequestInit) => {
      if (path === "/api/setup/state") return respond({ setup: true, password_set: false });
      if (path === "/api/setup/password" && init?.method === "POST") {
        return respond({ token: "row-121-setup-token" });
      }
      if (path === "/api/setup/public-url" && init?.method === "POST") {
        publicUrlSet = true;
        return respond({ ok: true });
      }
      if (path === "/api/setup/progress") {
        progressCalls += 1;
        if (progressCalls === 1) {
          await mountProbe;
          return respond({ detail: "not authenticated" }, 401);
        }
        return respond({ ...PROGRESS, public_url: publicUrlSet });
      }
      return respond({ ok: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await goToUrlStep();

    await act(async () => {
      releaseMountProbe();
    });

    expect(screen.queryByLabelText("Set the master password")).toBeNull();
    expect(screen.getByLabelText("Autoposter's own URL")).toBeInTheDocument();
    // ...and the wizard still moves: nothing was burned by the late answer.
    await submitPublicUrlStep();
    await waitFor(() => expect(screen.getByLabelText("Database URL")).toBeInTheDocument());
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
      progressMock({ ...PROGRESS, database: true, database_source: "resolved" }),
    );

    render(<Setup />);
    await goToUrlStep();
    await submitPublicUrlStep();

    await waitFor(() => expect(screen.getByTestId("systems-step")).toBeInTheDocument());
    expect(screen.queryByLabelText("Database URL")).toBeNull();
  });

  it("keeps the typed address when the server refuses it, and shows the fixed sentence", async () => {
    const detail =
      "this must be an http:// or https:// address with a host, with no username or password " +
      "in it, and with no query string or fragment";
    vi.stubGlobal(
      "fetch",
      progressMock(PROGRESS, (path, init) =>
        path === "/api/setup/public-url" && init?.method === "POST"
          ? respond({ detail }, 400)
          : undefined,
      ),
    );

    render(<Setup />);
    await goToUrlStep();
    fireEvent.change(screen.getByLabelText("Autoposter's own URL"), {
      target: { value: "autoposter.example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => screen.getByText(detail));
    expect(screen.getByLabelText<HTMLInputElement>("Autoposter's own URL").value).toBe(
      "autoposter.example.test",
    );
  });

  it("goes back into the database step it answered itself, with a Stored pill", async () => {
    // The step's own submit is what made `database` true, so keying the order
    // on the boolean deleted the step at the moment it was answered: Back from
    // the systems pane landed on the address pane and a mistyped DSN could not
    // be corrected for the life of the process.
    render(<Setup />);
    await goToSystemsStep();

    fireEvent.click(screen.getByRole("button", { name: "Back" }));

    await waitFor(() => expect(screen.getByLabelText("Database URL")).toBeInTheDocument());
    expect(screen.getByTestId("stored-setup-database")).toHaveTextContent("Stored");
    expect(screen.getByLabelText<HTMLInputElement>("Database URL").value).toBe("");
    expect(screen.getByRole("button", { name: "Test and continue" })).not.toBeDisabled();
  });

  it("stages the configuration from inside the Plex accordion, not from a box beside it", async () => {
    // Task 3 replaces the standalone "Plex server URL" field: the address is
    // the one the operator PICKED from their own account, and the tick-list
    // that comes with it is the same submit's `excluded_libraries`. This is the
    // case that field's coverage becomes -- the sign-in is offered where the
    // Plex credential is, and it is the whole of the configuration step now.
    vi.stubGlobal(
      "fetch",
      progressMock({ ...PROGRESS, config: true, config_source: "staged" }),
    );

    render(<Setup />);
    await goToSystemsStep();
    // Held, so it renders collapsed (facts C8) -- the sign-in lives inside it.
    fireEvent.click(screen.getByRole("button", { name: /Plex token/ }));

    await waitFor(() =>
      expect(screen.getByTestId("accordion-body-plex")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Sign in with Plex" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Plex server URL")).toBeNull();
  });

  it("offers no Plex submit on a deployment that provides its own document, and says why", async () => {
    // `POST /api/setup/config` is REFUSED while a document already resolves
    // (facts Amendment 6), so on a `configured` deployment the control that
    // would post to it is a button whose only outcome is a fixed refusal. It
    // is replaced by the sentence that explains it. The public URL's
    // stage-but-do-not-persist consolation does not apply here: the Plex
    // address and the excluded libraries live IN the document, and the
    // document is the deployment's. The sign-in is still offered, because it
    // still stages the account token.
    vi.stubGlobal(
      "fetch",
      progressMock({ ...PROGRESS, config: true, config_source: "configured" }),
    );

    render(<Setup />);
    await goToSystemsStep();
    fireEvent.click(screen.getByRole("button", { name: /Plex token/ }));

    await waitFor(() => expect(screen.getByTestId("accordion-body-plex")).toBeInTheDocument());
    expect(screen.getByTestId("plex-configured")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Plex token address"), {
      target: { value: "http://plex.invalid:32400" },
    });
    expect(screen.queryByRole("button", { name: "Use this address" })).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in with Plex" })).toBeInTheDocument();
  });

  it("reaches the configuration document from the accordion's own two fields", async () => {
    // Round-2 M13, and the positive half of the case above. The three pane
    // cases render `SetupPlexPane` with `address`/`credentialValue` as PROPS;
    // nothing until now asserted that `Setup.tsx` actually HANDS the accordion's
    // fields to it. If that wiring were dropped the pane's own tests would all
    // stay green and the Critical this branch closed -- a configuration
    // document reachable only behind a completed plex.tv sign-in -- would be
    // back. This is the line whose regression re-opens it, walked the way an
    // operator walks it: type the address and the token into the accordion,
    // read the libraries, submit.
    const fetchMock = progressMock(
      { ...PROGRESS, config_source: null },
      (path, init) => {
        if (path === "/api/setup/plex/libraries" && init?.method === "POST") {
          return respond({
            libraries: [
              { key: "1", title: "Movies", type: "movie" },
              { key: "2", title: "Photos", type: "photo" },
            ],
          });
        }
        if (path === "/api/setup/config" && init?.method === "POST") {
          return respond({ path: "/state/autoposter.yaml" });
        }
        return undefined;
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await goToSystemsStep();
    fireEvent.click(screen.getByRole("button", { name: /Plex token/ }));
    await waitFor(() => expect(screen.getByTestId("accordion-body-plex")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("Plex token address"), {
      target: { value: "http://plex.invalid:32400" },
    });
    fireEvent.change(screen.getByLabelText("AUTOPOSTER_PLEX_TOKEN"), {
      target: { value: "row-121-typed-token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Use this address" }));
    await waitFor(() => expect(screen.getByLabelText("Photos")).toBeChecked());

    // The read carried BOTH accordion fields -- the address the operator typed
    // and the token they pasted beside it, which is the whole of the manual
    // arrival.
    const read = fetchMock.mock.calls.find(([path]) => path === "/api/setup/plex/libraries");
    expect(JSON.parse(String(read?.[1]?.body))).toEqual({
      base_url: "http://plex.invalid:32400",
      credential_value: "row-121-typed-token",
    });

    fireEvent.click(screen.getByLabelText("Photos"));
    fireEvent.click(screen.getByRole("button", { name: "Use this server" }));

    // ...and it ends at the ONE writer of the configuration document.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([path]) => path === "/api/setup/config"),
      ).toBe(true),
    );
    const staged = fetchMock.mock.calls.find(([path]) => path === "/api/setup/config");
    expect(JSON.parse(String(staged?.[1]?.body))).toEqual({
      plex_url: "http://plex.invalid:32400",
      excluded_libraries: ["Photos"],
    });
  });

  it("registers the *arr webhook from its own panel and reports it on the finish page", async () => {
    // Task 4, through the real entry point rather than through the pane alone:
    // the control lives in the SONARR accordion's children slot, the result is
    // held by `Setup` and it is the FINISH pane that renders it, so the three
    // pieces only meet here. A pane test would pass with the button wired to
    // nothing and the finish pane rendering a registration nothing ever put in.
    const fetchMock = progressMock(
      {
        ...PROGRESS,
        database: true,
        database_source: "resolved",
        providers: {
          AUTOPOSTER_PLEX_TOKEN: "***REDACTED***",
          AUTOPOSTER_SONARR_APIKEY: "***REDACTED***",
        },
        required: [],
        config: true,
        config_source: "staged",
      },
      (path, init) => {
        if (path === "/api/setup/arr/webhook" && init?.method === "POST") {
          return respond({
            ok: true,
            action: "created",
            detail: "Sonarr accepted the webhook registration (created).",
          });
        }
        return undefined;
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<Setup />);
    await goToUrlStep();
    await submitPublicUrlStep();
    await waitFor(() => screen.getByTestId("systems-step"));
    fireEvent.click(screen.getByRole("button", { name: /Sonarr API key/ }));
    await waitFor(() => expect(screen.getByTestId("accordion-body-sonarr")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Register the webhook for me" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([path]) => path === "/api/setup/arr/webhook"),
      ).toBe(true),
    );
    const asked = fetchMock.mock.calls.find(([path]) => path === "/api/setup/arr/webhook");
    expect(JSON.parse(String(asked?.[1]?.body))).toEqual({ service: "sonarr" });

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    // The address the finish page shows is the one the URL step submitted: the
    // server never serves it back, so the page holds it from its own submit.
    await waitFor(() =>
      expect(screen.getByTestId("registration-sonarr")).toHaveTextContent(
        "https://autoposter.example.test/webhook/sonarr",
      ),
    );
    expect(screen.getByTestId("registration-radarr")).toHaveTextContent("Not attempted");
  });
});
