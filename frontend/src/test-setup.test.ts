/** Guards the guard.
 *
 * `src/test-setup.ts` replaces `fetch` with one that throws, and
 * `vite.config.ts` sets `unstubGlobals` so a stub set by one test is undone
 * before the next. Neither has a visible effect while every test remembers to
 * stub, which is precisely how both get deleted as unused.
 */
import { expect, it, vi } from "vitest";

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
