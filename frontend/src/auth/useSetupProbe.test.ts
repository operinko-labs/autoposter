import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useSetupProbe } from "./useSetupProbe";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useSetupProbe", () => {
  it("resolves to the state the server answers with", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () => ({ ok: true, status: 200, json: async () => ({ setup: true }) }) as Response,
      ),
    );

    const { result } = renderHook(() => useSetupProbe());

    await waitFor(() => expect(result.current).toBe(true));
  });

  it("resolves to false, not to an indefinite null, when the probe hangs past its timeout", async () => {
    // A request that never settles -- a stalled reverse proxy, a pod
    // mid-cold-start -- rather than one that rejects outright. Only the
    // AbortController the hook wires up can end this.
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_path: unknown, init?: RequestInit) =>
          new Promise((_, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("The operation was aborted.", "AbortError")),
            );
          }),
      ),
    );

    const { result } = renderHook(() => useSetupProbe());

    expect(result.current).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });

    expect(result.current).toBe(false);
  });
});
