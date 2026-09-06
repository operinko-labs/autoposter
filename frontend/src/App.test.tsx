import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
});

describe("the gate", () => {
  it("renders the wizard, not the login form, on an unconfigured deployment", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        ({ ok: true, status: 200, json: async () => ({ setup: true, password_set: false }) }) as Response,
      ),
    );

    render(<App />);

    await waitFor(() =>
      expect(screen.getByLabelText("Master password")).toBeInTheDocument(),
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
