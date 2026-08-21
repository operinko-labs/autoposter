import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  getToken,
  setToken,
  setUnauthorizedHandler,
} from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  setToken(null);
  setUnauthorizedHandler(null);
  window.sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiFetch", () => {
  it("sends the bearer token once one is set", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    setToken("abc123");

    await apiFetch("/api/status");

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer abc123");
  });

  it("sends no Authorization header when there is no session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/status");

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.has("Authorization")).toBe(false);
  });

  it("clears the session and notifies on a 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 401)));
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setToken("expired");

    await expect(apiFetch("/api/status")).rejects.toBeInstanceOf(ApiError);

    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("clears the token before notifying, so the login page cannot resend it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 401)));
    let tokenSeenByHandler: string | null = "not called";
    setUnauthorizedHandler(() => {
      tokenSeenByHandler = getToken();
    });
    setToken("expired");

    await expect(apiFetch("/api/status")).rejects.toThrow();

    expect(tokenSeenByHandler).toBeNull();
  });

  it("never writes the token to localStorage", () => {
    // Asserted against a stub rather than the real thing: Node 26 defines its
    // own experimental localStorage that is undefined unless the process was
    // started with --localstorage-file, and it shadows the one jsdom provides.
    // Stubbing pins the invariant -- nothing reaches localStorage -- without
    // depending on which of the two the environment exposes.
    const fake = {
      getItem: vi.fn(),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
      key: vi.fn(),
      length: 0,
    };
    vi.stubGlobal("localStorage", fake);

    setToken("secret-token");

    expect(fake.setItem).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem("autoposter.token")).toBe("secret-token");
  });

  it("removes the stored token when the session is cleared", () => {
    setToken("secret-token");
    setToken(null);

    expect(window.sessionStorage.getItem("autoposter.token")).toBeNull();
  });

  it("surfaces the API's detail message on a non-401 error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "job not found" }, 404)),
    );

    await expect(apiFetch("/api/jobs/9/retry", { method: "POST" })).rejects.toThrow(
      "job not found",
    );
  });

  it("does not surface a non-JSON error body verbatim", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<html>502 Bad Gateway</html>", { status: 502 }),
      ),
    );

    await expect(apiFetch("/api/status")).rejects.toThrow("request failed with 502");
  });

  it("sets a JSON content type when sending a body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/login", {
      method: "POST",
      body: JSON.stringify({ password: "x" }),
    });

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});
