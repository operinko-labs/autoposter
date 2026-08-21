import "@testing-library/jest-dom/vitest";

/** No test may make a real outbound request.
 *
 * This is the frontend half of the autouse `no_outbound_network` fixture in
 * tests/conftest.py. jsdom leaves Node's real `fetch` in place, so a test that
 * forgets to stub it does not fail -- it quietly opens a socket, and in CI it
 * hangs until the runner's timeout instead of pointing at the missing stub.
 *
 * Assigned before any test runs, so `vi.stubGlobal("fetch", ...)` records this
 * as the value to restore and `unstubGlobals` puts the guard back afterwards.
 */
globalThis.fetch = (async (input: RequestInfo | URL) => {
  // `Request.prototype.toString()` yields the useless "[object Request]" --
  // it does not override `Object.prototype.toString`. `input.url` is the
  // actual address, which is the whole point of this diagnostic.
  const url = input instanceof Request ? input.url : String(input);
  throw new Error(
    `tests must not make real network calls; fetch("${url}") was attempted. ` +
      "Stub it with vi.stubGlobal(\"fetch\", ...).",
  );
}) as typeof fetch;
