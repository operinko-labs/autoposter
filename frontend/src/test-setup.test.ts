/** Guards the guard.
 *
 * `src/test-setup.ts` replaces `fetch` with one that throws, and
 * `vite.config.ts` sets `unstubGlobals` so a stub set by one test is undone
 * before the next. Neither has a visible effect while every test remembers to
 * stub, which is precisely how both get deleted as unused.
 */
import { expect, it, vi } from "vitest";

import { apiFetch } from "./api/client";

it("lets a test stub fetch for itself", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));

  await expect(fetch("/api/status")).resolves.toBeInstanceOf(Response);
});

it("puts the network guard back for the next test", async () => {
  // Ordering is the point: the stub above must not still be in place here,
  // and what replaces it must refuse to reach the network.
  await expect(fetch("https://api.themoviedb.org/3/movie/1")).rejects.toThrow(
    /tests must not make real network calls/,
  );
});

it("leaves a GET pending when it ends", () => {
  // Sidebar.test.tsx's default stub does exactly this for every test.
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

  void apiFetch("/api/status");
});

it("does not hand the next test the request the last one left pending", async () => {
  // Ordering is the point again: apiFetch shares an in-flight GET with later
  // callers of the same path, so without test-setup.ts clearing that between
  // tests this read would join the dead request above and never settle.
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true })));
  vi.stubGlobal("fetch", fetchMock);

  await expect(apiFetch("/api/status")).resolves.toEqual({ ok: true });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
