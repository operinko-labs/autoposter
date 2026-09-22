import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { App } from "./App";

// Login and Setup are lazy chunks (perf spec A3). Loading them once here
// keeps a cold module transform -- Setup pulls in every wizard pane -- out of
// each test's one-second waitFor budget.
beforeAll(async () => {
  await Promise.all([import("./pages/Login"), import("./pages/Setup")]);
}, 20000);

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("the gate", () => {
  it("renders the wizard, not the login form, on an unconfigured deployment", async () => {
    // Branched by path rather than answered identically to every call: the
    // state probe and Setup's own progress fetch are two different routes,
    // and giving both the state-shaped body would hand Setup a malformed
    // `progress` (no `providers`) instead of the 401 an absent token really
    // produces.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: unknown) =>
        path === "/api/setup/state"
          ? ({ ok: true, status: 200, json: async () => ({ setup: true, password_set: false }) }) as Response
          : ({ ok: false, status: 401, json: async () => ({ detail: "not authenticated" }) }) as Response,
      ),
    );

    render(<App />);

    await waitFor(() =>
      expect(screen.getByLabelText("Set the master password")).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  });

  it("renders the login form when the probe says the deployment is configured", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        ({ ok: true, status: 200, json: async () => ({ setup: false }) }) as Response,
      ),
    );

    render(<App />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument(),
    );
  });

  it("falls back to the login form when the probe fails outright", async () => {
    // An older server has no such route. Rendering the login page is the
    // correct, closed default: it is what the bundle did before this probe.
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));

    render(<App />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument(),
    );
  });
});

describe("a page whose chunk fails to load", () => {
  afterEach(() => {
    vi.doUnmock("./pages/Dashboard");
    vi.restoreAllMocks();
  });

  it("says so in the page area and keeps the sidebar", async () => {
    // What a tab still running the previous deploy's entry meets: the page's
    // chunk name no longer exists on the server, so its import() rejects.
    // A fresh App (and a fresh module graph) so its React.lazy has not
    // already resolved Dashboard from the real module.
    vi.resetModules();
    vi.doMock("./pages/Dashboard", () => {
      throw new Error("Failed to fetch dynamically imported module");
    });
    // React logs the error the boundary catches; that is expected here.
    vi.spyOn(console, "error").mockImplementation(() => {});
    window.sessionStorage.setItem("autoposter.token", "a-session-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        ({
          ok: true,
          status: 200,
          json: async () => ({
            jobs_by_state: {},
            workers: 1,
            processed_last_24h: 0,
            scheduled_jobs: [],
          }),
        }) as Response,
      ),
    );
    const { App: FreshApp } = await import("./App");

    render(<FreshApp />);

    expect(
      await screen.findByText("This page failed to load.", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    // Without a boundary React 19 unmounts the whole root: the sidebar going
    // with it is the blank tab this guards against.
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();

    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
