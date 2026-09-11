import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SetupFinishPane } from "./SetupFinishPane";
import type { SetupProgress } from "../api/setup";

/** A deployment the wizard is writing the document for: `config_source` is
 * `"staged"`, which is the ONE value that means `finish` will persist
 * `public_url` -- `finish` writes the document only
 * `if state.config_document is not None`, and that is exactly what `"staged"`
 * reports. `"configured"` and `"state"` both mean a document already resolves,
 * the wizard holds none, and nothing of `public_url` is written. */
const PROGRESS: SetupProgress = {
  password: true,
  database: true,
  database_source: "resolved",
  providers: {
    AUTOPOSTER_PLEX_TOKEN: "***REDACTED***",
    AUTOPOSTER_MDBLIST_APIKEY: null,
    // Both *arr services are configured in the base fixture -- staged key,
    // checked address -- so the existing "not attempted" cases below stay
    // about an operator who simply did not press the button. The one test
    // for "not configured" overrides these two fields directly.
    AUTOPOSTER_SONARR_APIKEY: "***REDACTED***",
    AUTOPOSTER_RADARR_APIKEY: "***REDACTED***",
  },
  required: [],
  config: true,
  config_source: "staged",
  public_url: true,
  checked_systems: ["sonarr", "radarr"],
};

function renderPane(overrides: Partial<Parameters<typeof SetupFinishPane>[0]> = {}) {
  return render(
    <SetupFinishPane
      busy={false}
      progress={PROGRESS}
      publicUrl="https://autoposter.example.test"
      webhookSecret="row-121-generated-secret"
      registrations={{}}
      onSubmit={vi.fn()}
      {...overrides}
    />,
  );
}

describe("SetupFinishPane", () => {
  it("shows the generated secret once, with the warning that it will not return", () => {
    renderPane();

    expect(screen.getByTestId("webhook-secret-value")).toHaveTextContent(
      "row-121-generated-secret",
    );
    expect(screen.getByText(/will not be shown again/i)).toBeInTheDocument();
  });

  it("lists each registration with the URL that was registered, and never the header", () => {
    // The url is shown because it is NOT a credential: it is `public_url` plus a
    // fixed path, and the operator has to be able to check it against what
    // their reverse proxy actually serves. The header VALUE is the secret and
    // is shown in exactly one place on this page -- the block above -- so the
    // header name has no business appearing beside a registration row.
    renderPane({
      registrations: {
        sonarr: {
          ok: true,
          action: "created",
          detail: "Sonarr accepted the webhook registration (created).",
        },
        radarr: { ok: false, action: null, detail: "Radarr refused the credential." },
      },
    });

    expect(screen.getByTestId("registration-sonarr")).toHaveTextContent(
      "https://autoposter.example.test/webhook/sonarr",
    );
    expect(screen.getByTestId("registration-radarr")).toHaveTextContent(
      "Radarr refused the credential.",
    );
    expect(document.body.textContent).not.toContain("X-Autoposter-Token");
  });

  it("distinguishes an updated registration from a created one, because C2a asks it to", () => {
    renderPane({
      registrations: {
        sonarr: {
          ok: true,
          action: "updated",
          detail: "Sonarr accepted the webhook registration (updated).",
        },
      },
    });

    expect(screen.getByTestId("registration-sonarr")).toHaveTextContent("updated");
  });

  it("reports a service that was never attempted as not attempted", () => {
    renderPane({ registrations: {} });

    expect(screen.getByTestId("registration-sonarr")).toHaveTextContent("Not attempted");
    expect(screen.getByTestId("registration-radarr")).toHaveTextContent("Not attempted");
  });

  it("reports a service with no staged key or no checked address as not configured", () => {
    // The Missing, task 4 review: a deployment that does not run Radarr at all
    // gets told to go configure it by hand, which is the wrong instruction.
    // Two distinct causes, both routed to the same fourth state -- no key
    // (radarr) and no checked address (sonarr).
    renderPane({
      progress: {
        ...PROGRESS,
        providers: { ...PROGRESS.providers, AUTOPOSTER_RADARR_APIKEY: null },
        checked_systems: ["radarr"],
      },
      registrations: {},
    });

    expect(screen.getByTestId("registration-sonarr")).toHaveTextContent("Not configured");
    expect(screen.getByTestId("registration-radarr")).toHaveTextContent("Not configured");
  });

  it("lists every credential left empty by its human label and its environment name", () => {
    renderPane();

    const skipped = screen.getByTestId("skipped-list");
    expect(skipped).toHaveTextContent("MDBList API key");
    expect(skipped).toHaveTextContent("AUTOPOSTER_MDBLIST_APIKEY");
    expect(skipped).not.toHaveTextContent("AUTOPOSTER_PLEX_TOKEN");
  });

  it("names public_url when the deployment's own document means it cannot be written", () => {
    renderPane({ progress: { ...PROGRESS, config_source: "configured" } });

    const skipped = screen.getByTestId("skipped-list");
    expect(skipped).toHaveTextContent("public_url");
    expect(skipped).toHaveTextContent(/registrations above but not written/i);
  });

  it("names public_url on a document an EARLIER FINISH wrote, for the same reason", () => {
    // `"state"` is the other resolving source and it is reachable by design --
    // Amendment 3's survivable window. `finish` writes the document only
    // `if state.config_document is not None`, and `stage_config_document`
    // refuses to stage one while a document resolves, so nothing of
    // `public_url` lands here either. Saying otherwise on this page would be
    // the one place the operator could not find out.
    renderPane({ progress: { ...PROGRESS, config_source: "state" } });

    const skipped = screen.getByTestId("skipped-list");
    expect(skipped).toHaveTextContent("public_url");
    expect(skipped).toHaveTextContent(/registrations above but not written/i);
  });

  it("says what a resolving document did and did not keep from the Plex panel", () => {
    // Facts C1 and the Task 3 review's third point. Three separate facts, and
    // the operator leaves believing the tick-list took effect unless all three
    // are said: `public_url` was used and not written; the Plex address and the
    // ticked libraries were recorded NOWHERE (`base_urls` dies with the setup
    // state at `execv`); and the account token DID persist, into the secrets
    // file, under both names.
    renderPane({ progress: { ...PROGRESS, config_source: "configured" } });

    const skipped = screen.getByTestId("skipped-list");
    expect(skipped).toHaveTextContent(/Plex address/i);
    expect(skipped).toHaveTextContent(/libraries/i);
    expect(skipped).toHaveTextContent(/were not recorded/i);
    expect(skipped).toHaveTextContent(/Plex token was stored/i);
  });

  it("does not claim public_url was skipped when the wizard is writing the document", () => {
    renderPane();

    expect(screen.getByTestId("skipped-list")).not.toHaveTextContent("public_url");
  });

  it("says the database step was skipped because the environment already resolved one", () => {
    renderPane();

    expect(screen.getByTestId("skipped-list")).toHaveTextContent(/database/i);
  });

  it("does not call the database skipped when the wizard staged it itself", () => {
    // `progress.database` is true either way -- the boolean says the step is
    // MET. `database_source` is the word that says WHO answered it, and it is
    // the one this sentence is about: a deployment whose DSN the operator typed
    // into step 3 did not skip anything.
    renderPane({ progress: { ...PROGRESS, database_source: "staged" } });

    expect(screen.getByTestId("skipped-list")).not.toHaveTextContent(/database/i);
  });

  it("keeps the finish button and its restart sentence", () => {
    renderPane();

    expect(screen.getByRole("button", { name: "Start autoposter" })).toBeInTheDocument();
    expect(screen.getByText(/restarts this service once/i)).toBeInTheDocument();
  });
});
