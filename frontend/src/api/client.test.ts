import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  apiFetch,
  apiFetchNdjson,
  apiPostForImage,
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

  it("leaves a FormData body's content type to the browser", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const body = new FormData();
    body.append("file", new File(["png-bytes"], "art.png", { type: "image/png" }));

    await apiFetch("/api/items/3/renders/poster/manual/upload", {
      method: "POST",
      body,
    });

    // Not merely "not application/json": ANY Content-Type set here is wrong.
    // A multipart body is unreadable without the boundary parameter, and only
    // the browser knows the boundary it is about to write.
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.has("Content-Type")).toBe(false);
    expect(fetchMock.mock.calls[0][1].body).toBe(body);
  });
});

describe("apiPostForImage", () => {
  it("posts the body as JSON with the bearer header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("bytes", { status: 200, headers: { "Content-Type": "image/jpeg" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    setToken("abc123");

    await apiPostForImage("/api/testing/sample", { art_kind: "poster", length: "short" });

    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/testing/sample");
    expect(init.method).toBe("POST");
    const headers = init.headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer abc123");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ art_kind: "poster", length: "short" });
  });

  it("returns the blob when the answer is an image", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("jpeg-bytes", { status: 200, headers: { "Content-Type": "image/jpeg" } }),
      ),
    );

    const result = await apiPostForImage("/api/testing/sample", {});

    expect("blob" in result).toBe(true);
    const blob = (result as { blob: Blob }).blob;
    expect(await blob.text()).toBe("jpeg-bytes");
  });

  it("returns the parsed JSON when the answer is not an image", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ truncated: true, art_kind: "poster", length: "long" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const result = await apiPostForImage("/api/testing/sample", {});

    expect("json" in result).toBe(true);
    expect((result as { json: unknown }).json).toEqual({
      truncated: true,
      art_kind: "poster",
      length: "long",
    });
  });

  it("clears the session and notifies on a 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 401)));
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setToken("expired");

    await expect(apiPostForImage("/api/testing/sample", {})).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
    });
    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("surfaces the API's detail message on a non-401 error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "no HTTP client on this instance" }, 503)),
    );

    await expect(apiPostForImage("/api/testing/sample", {})).rejects.toThrow(
      "no HTTP client on this instance",
    );
  });
});

/** A `ReadableStreamDefaultReader` stand-in whose `read()` the test drives by
 * hand: each call returns a promise the test resolves or rejects on its own
 * schedule, which is what it takes to land a chunk boundary mid-line or make
 * a pending read reject the way an aborted real fetch body does. */
function controllableReader() {
  let settle: {
    resolve: (result: ReadableStreamReadResult<Uint8Array>) => void;
    reject: (reason: unknown) => void;
  } | null = null;

  const reader = {
    read: () =>
      new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
        settle = { resolve, reject };
      }),
    releaseLock: vi.fn(),
  };

  const encoder = new TextEncoder();
  return {
    reader,
    push(chunk: string) {
      settle!.resolve({ done: false, value: encoder.encode(chunk) });
    },
    end() {
      settle!.resolve({ done: true, value: undefined });
    },
    fail(reason: unknown) {
      settle!.reject(reason);
    },
  };
}

/** Lets a suspended `await reader.read()` inside apiFetchNdjson resume and
 * run synchronously up to its next await, so the next push()/end()/fail()
 * lands on the read the loop is actually waiting on. */
async function flush(): Promise<void> {
  for (let i = 0; i < 3; i++) await Promise.resolve();
}

describe("apiFetchNdjson", () => {
  it("joins a line split across chunks and still delivers adjacent whole lines", async () => {
    const { reader, push, end } = controllableReader();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        body: { getReader: () => reader },
      }),
    );
    const onValue = vi.fn();

    const promise = apiFetchNdjson("/api/logs/stream", onValue, new AbortController().signal);
    await flush();

    push('{"a":1');
    await flush();
    expect(onValue).not.toHaveBeenCalled(); // the line is not complete yet

    push('}\n{"b":2}\n{"c":3}\n');
    await flush();
    end();
    await promise;

    expect(onValue.mock.calls).toEqual([[{ a: 1 }], [{ b: 2 }], [{ c: 3 }]]);
  });

  it("clears the session, notifies, and throws ApiError(401) on a 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 401, ok: false }));
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setToken("expired");

    await expect(
      apiFetchNdjson("/api/logs/stream", vi.fn(), new AbortController().signal),
    ).rejects.toMatchObject({ name: "ApiError", status: 401 });

    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("resolves, rather than rejects, when the pending read is aborted", async () => {
    const { reader, fail } = controllableReader();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        body: { getReader: () => reader },
      }),
    );
    const controller = new AbortController();

    const promise = apiFetchNdjson("/api/logs/stream", vi.fn(), controller.signal);
    await flush();

    controller.abort();
    fail(new DOMException("aborted", "AbortError"));

    await expect(promise).resolves.toBeUndefined();
  });

  it("throws a fixed sentence, not the parser's excerpt, when a line is not JSON", async () => {
    const { reader, push } = controllableReader();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        body: { getReader: () => reader },
      }),
    );
    const onValue = vi.fn();

    const promise = apiFetchNdjson("/api/logs/stream", onValue, new AbortController().signal);
    await flush();
    push("<html>502 Bad Gateway from edge.internal</html>\n");

    // A SyntaxError's message quotes the bytes that did not parse; the page
    // renders whatever message reaches it, so the reader owns the wording.
    await expect(promise).rejects.toMatchObject({
      name: "ApiError",
      status: 200,
      message: "the server answered with something that was not JSON",
    });
    expect(onValue).not.toHaveBeenCalled();
    expect(reader.releaseLock).toHaveBeenCalledOnce();
  });
});

describe("apiFetch coalescing", () => {
  it("sends concurrent GETs of one path as one request, each caller its own copy", async () => {
    // A fresh Response per call, so the un-coalesced code fails on the count
    // below rather than hanging on a body read twice.
    const fetchMock = vi.fn(async () => jsonResponse({ workers: 5, libraries: ["Movies"] }));
    vi.stubGlobal("fetch", fetchMock);

    // All four are issued before any of them can settle: that is "concurrent".
    const results = await Promise.all(
      [1, 2, 3, 4].map(() => apiFetch<{ workers: number; libraries: string[] }>("/api/config")),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    for (const result of results) {
      expect(result).toEqual({ workers: 5, libraries: ["Movies"] });
    }
    // structuredClone per caller: one panel mutating its copy cannot reach
    // another panel's.
    expect(new Set(results).size).toBe(4);
    results[0].libraries.push("TV Shows");
    expect(results[1].libraries).toEqual(["Movies"]);
  });

  it("fetches again once the shared request has settled", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ workers: 5 }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/config");
    await apiFetch("/api/config");

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("hands a failure to every caller", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ detail: "the database is unreachable" }, 500),
    );
    vi.stubGlobal("fetch", fetchMock);

    const outcomes = await Promise.allSettled(
      [1, 2, 3, 4].map(() => apiFetch("/api/config")),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    for (const outcome of outcomes) {
      expect(outcome.status).toBe("rejected");
      const reason = (outcome as PromiseRejectedResult).reason;
      expect(reason).toBeInstanceOf(ApiError);
      expect(reason.message).toBe("the database is unreachable");
    }
  });

  it("fails every caller on a 401 and drops the session once", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({}, 401));
    vi.stubGlobal("fetch", fetchMock);
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    setToken("expired");

    const outcomes = await Promise.allSettled([apiFetch("/api/status"), apiFetch("/api/status")]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(outcomes.map((outcome) => outcome.status)).toEqual(["rejected", "rejected"]);
    expect(getToken()).toBeNull();
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });

  it("never joins a write, a request with its own options, or another path", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([
      apiFetch("/api/config/overrides", { method: "PUT", body: "{}" }),
      apiFetch("/api/config/overrides", { method: "PUT", body: "{}" }),
      apiFetch("/api/config", { cache: "no-cache" }),
      apiFetch("/api/config", { cache: "no-cache" }),
      apiFetch("/api/status"),
      apiFetch("/api/events"),
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(6);
  });
});
