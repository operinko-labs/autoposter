import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { SessionProvider } from "../auth/SessionContext";
import { Login, loginErrorMessage } from "./Login";

describe("loginErrorMessage", () => {
  it("reports a rejected password for a 401, and says nothing more", () => {
    expect(loginErrorMessage(new ApiError(401, "invalid password"))).toBe(
      "Incorrect password.",
    );
  });

  it("reports rate limiting separately, because waiting is the fix", () => {
    expect(loginErrorMessage(new ApiError(429, "slow down"))).toContain(
      "Too many attempts",
    );
  });

  it("does not blame the password for a server error", () => {
    // A user whose server is down was previously told their password was
    // wrong, and went looking for a typo that did not exist.
    const message = loginErrorMessage(new ApiError(500, "boom"));
    expect(message).not.toContain("password");
    expect(message).toContain("500");
  });

  it("does not blame the password when the request never arrived", () => {
    const message = loginErrorMessage(new TypeError("Failed to fetch"));
    expect(message).not.toContain("password");
    expect(message).toContain("Could not reach the server");
  });
});

describe("Login", () => {
  it("shows the brand mark above the form", () => {
    render(
      <SessionProvider>
        <Login />
      </SessionProvider>,
    );

    expect(screen.getByRole("img", { name: "Autoposter" })).toBeInTheDocument();
  });
});
