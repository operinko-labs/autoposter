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
import type { Root } from "react-dom/client";
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
  vi.doUnmock("react-dom/client");
});

/** Imports the real entry point, capturing the React root it creates so the
 * test can unmount it afterwards. Without this, `main.tsx`'s `createRoot`
 * call is invisible to the test -- `afterEach` above only clears the DOM, it
 * does not stop React -- so Dashboard's 5-second poll interval outlives the
 * test. It never fired against the throwing `fetch` `test-setup.ts` restores
 * between tests only because this file finishes in ~1.4s; that is a latent
 * flake, not a guarantee.
 *
 * `vi.spyOn` cannot patch this: `react-dom/client`'s namespace object is not
 * configurable under native ESM, so reassigning `createRoot` on it throws.
 * `vi.doMock` sidesteps that by substituting the module at resolution time,
 * before `main.tsx` ever imports it, rather than mutating it afterwards. */
async function mountMain(): Promise<Root> {
  let root: Root | undefined;
  vi.doMock("react-dom/client", async (importOriginal) => {
    const actual = await importOriginal<typeof import("react-dom/client")>();
    return {
      ...actual,
      createRoot: (...args: Parameters<typeof actual.createRoot>) => {
        root = actual.createRoot(...args);
        return root;
      },
    };
  });

  await act(async () => {
    await import("./main");
  });

  if (!root) throw new Error("main.tsx did not call createRoot");
  return root;
}

it("mounts the application at #root, not a placeholder", async () => {
  const root = await mountMain();

  // No session, so the gate renders the login form. Any of this is only in
  // the document if main.tsx mounted App.
  // Login is its own lazy chunk (perf spec A3), and beforeEach's
  // vi.resetModules() makes this file load it cold every time -- hence the
  // explicit budget rather than findBy's one-second default.
  expect(
    await screen.findByLabelText("Password", {}, { timeout: 5000 }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();

  await act(async () => root.unmount());
});

it("mounts the routed shell when a session already exists", async () => {
  // Guards the other half of the same failure: an entry point that mounts
  // something, but a router that reaches none of the pages.
  window.sessionStorage.setItem("autoposter.token", "a-session-token");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.startsWith("/api/events")) return jsonResponse({ events: [] });
      // RunCharts' own mount-time fetch, answered so its request does not
      // fall through to the status-shaped fallback below and hand the chart
      // a body with no `runs` array.
      if (path.startsWith("/api/stats/runs")) {
        return jsonResponse({ runs: [], generated_at: "2026-01-02T03:04:05Z" });
      }
      return jsonResponse({
        jobs_by_state: {
          pending: 0, running: 0, deferred: 0, done: 0,
          failed: 0, parked: 0, dismissed: 0,
        },
        workers: 1,
        processed_last_24h: 0,
        scheduled_jobs: [],
      });
    }),
  );

  const root = await mountMain();

  expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Library" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Failures" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  // The sidebar is part of the entry; the page is a lazy chunk.
  expect(
    await screen.findByRole("heading", { name: "Dashboard" }, { timeout: 5000 }),
  ).toBeInTheDocument();

  await act(async () => root.unmount());
});
