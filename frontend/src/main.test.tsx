/** The entry point must actually mount the application.
 *
 * This file exists because it did not. `main.tsx` rendered a placeholder
 * paragraph and never imported `App`, so every page, route and provider was
 * dead code in the shipped bundle -- and nothing caught it: `tsc --noEmit`
 * typechecks all of `src/` whatever the import graph looks like, and the
 * production build succeeded because building a bundle that reaches nothing
 * is still a successful build.
 *
 * So the assertion here is deliberately not "App renders". It is "importing
 * the real entry point puts the real application in the document".
 */
import { act, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.resetModules();
  window.sessionStorage.clear();
  document.body.innerHTML = '<div id="root"></div>';
});

afterEach(() => {
  document.body.innerHTML = "";
});

it("mounts the application at #root, not a placeholder", async () => {
  await act(async () => {
    await import("./main");
  });

  // No session, so the gate renders the login form. Any of this is only in
  // the document if main.tsx mounted App.
  expect(screen.getByLabelText("Password")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
});

it("mounts the routed shell when a session already exists", async () => {
  // Guards the other half of the same failure: an entry point that mounts
  // something, but a router that reaches none of the pages.
  window.sessionStorage.setItem("autoposter.token", "a-session-token");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) =>
      path.startsWith("/api/events")
        ? jsonResponse({ events: [] })
        : jsonResponse({
            jobs_by_state: {
              pending: 0, running: 0, done: 0,
              failed: 0, parked: 0, dismissed: 0,
            },
            workers: 1,
            processed_last_24h: 0,
            scheduled_jobs: [],
          }),
    ),
  );

  await act(async () => {
    await import("./main");
  });

  expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Failures" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
});
