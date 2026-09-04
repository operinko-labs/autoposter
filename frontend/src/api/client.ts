/** The single place the API is spoken to.
 *
 * Two things are centralised here on purpose:
 *
 *  - The bearer token. Held in a module-level variable rather than read from
 *    storage per call, so there is one definition of "the current session".
 *  - The 401. Every authenticated endpoint can return one at any time -- the
 *    session has a server-side expiry -- and handling it per page means one
 *    page eventually forgets and renders an error where a login form belongs.
 *    `apiFetch` notifies a single subscriber instead.
 *
 * The live pages (the log tail and the dashboard) stream through
 * `apiFetchNdjson` below rather than through a WebSocket, because the session
 * travels only as an `Authorization` header and a browser `WebSocket` cannot
 * send one -- the same constraint that shapes `apiFetchImage`.
 */

const TOKEN_KEY = "autoposter.token";

/** sessionStorage, never localStorage: this scopes the token to the tab and
 * drops it when the tab closes. A token in localStorage outlives the browsing
 * session and is readable by any script that gets injected into the origin. */
function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    // Blocked by a privacy setting; the in-memory token still works for the
    // life of the page.
    return null;
  }
}

let token: string | null = storage()?.getItem(TOKEN_KEY) ?? null;
let onUnauthorized: (() => void) | null = null;

export function getToken(): string | null {
  return token;
}

export function setToken(next: string | null): void {
  token = next;
  const store = storage();
  if (!store) return;
  if (next === null) store.removeItem(TOKEN_KEY);
  else store.setItem(TOKEN_KEY, next);
}

/** Registered once by the session provider. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

export class ApiError extends Error {
  readonly status: number;
  /** The `detail` body as the server sent it, kept alongside the flattened
   * message because a 422 from the config editor is a *list* of per-field
   * errors: the page has to place each one at the field its path names, and
   * a single string cannot say which field it belongs to. */
  readonly detail: unknown;

  constructor(status: number, message: string, detail: unknown = undefined) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, { ...init, headers });

  if (response.status === 401) {
    // Drop the token before notifying: the handler routes to the login page,
    // which must not then send the dead token with its own first request.
    setToken(null);
    onUnauthorized?.();
    throw new ApiError(401, "not authenticated");
  }

  if (!response.ok) {
    const { message, detail } = await errorBody(response);
    throw new ApiError(response.status, message, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Image bytes rather than JSON, for the artwork endpoints.
 *
 * This exists because `<img src="/api/items/3/artwork/poster">` cannot work:
 * `require_session` in src/autoposter/api/auth.py accepts the session only as
 * an `Authorization: Bearer` header, and a browser sends no such header for an
 * image it loads itself. There is no cookie to fall back on. So the bytes are
 * fetched here, with the header, and handed to the <img> as an object URL.
 *
 * `null`, not a throw, for a 404: an item whose art has not been rendered yet
 * legitimately has no file, and that is the common case in a library still
 * being worked through -- not an error a caller should have to distinguish
 * from a real one.
 */
export async function apiFetchImage(path: string): Promise<Blob | null> {
  const headers = new Headers();
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(path, { headers });

  if (response.status === 401) {
    // Same contract as apiFetch: the session is dead, so drop it and let the
    // one subscriber route to the login form.
    setToken(null);
    onUnauthorized?.();
    throw new ApiError(401, "not authenticated");
  }

  if (response.status === 404) return null;

  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }

  return await response.blob();
}

/** POST a JSON body to an endpoint that answers with EITHER an image or JSON.
 *
 * The testing sample endpoint (src/autoposter/api/testing.py) returns the
 * styled JPEG bytes when the title fits and a small JSON body reporting the
 * truncation outcome when it does not -- one endpoint, two content types. A
 * `<img src>` cannot carry the bearer header require_session needs, and it
 * could not tell the two outcomes apart anyway, so the POST is made here and
 * the caller is handed whichever came back.
 *
 * `apiFetchImage` is GET-only and returns a bare `Blob | null`; this shares
 * its bearer header and 401 handling but must post a body and preserve the
 * image-or-JSON fork, so it returns a tagged result rather than a bare blob.
 */
export async function apiPostForImage(
  path: string,
  body: unknown,
): Promise<{ blob: Blob } | { json: unknown }> {
  const headers = new Headers();
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);
  headers.set("Content-Type", "application/json");

  const response = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  if (response.status === 401) {
    // Same contract as apiFetch: drop the dead session and let the one
    // subscriber route to the login form.
    setToken(null);
    onUnauthorized?.();
    throw new ApiError(401, "not authenticated");
  }

  if (!response.ok) {
    const { message, detail } = await errorBody(response);
    throw new ApiError(response.status, message, detail);
  }

  // The fork is on the Content-Type the server actually sent, not on the
  // status: both outcomes are a 200. An `image/*` answer is the styled bytes;
  // anything else is the JSON outcome (currently the truncation report).
  const contentType = response.headers.get("Content-Type") ?? "";
  if (contentType.startsWith("image/")) {
    return { blob: await response.blob() };
  }
  return { json: await response.json() };
}

/** An NDJSON stream rather than a JSON body, for the log tail.
 *
 * This exists for the same reason as `apiFetchImage`: the session travels
 * only as an `Authorization: Bearer` header, which rules out `EventSource`,
 * so the stream is fetched here with the header and read line by line. Each
 * parsed object is handed to `onValue` as it arrives; the promise resolves
 * when the server ends the stream and rejects on a transport error, so the
 * caller owns reconnecting. The signal aborts the read mid-stream -- an
 * abort resolves rather than rejects, because the caller asked for it.
 */
export async function apiFetchNdjson(
  path: string,
  onValue: (value: unknown) => void,
  signal: AbortSignal,
): Promise<void> {
  const headers = new Headers();
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(path, { headers, signal });

  if (response.status === 401) {
    // Same contract as apiFetch: the session is dead, so drop it and let the
    // one subscriber route to the login form.
    setToken(null);
    onUnauthorized?.();
    throw new ApiError(401, "not authenticated");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }

  if (response.body === null) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true });
      const parts = buffered.split("\n");
      buffered = parts.pop() ?? "";
      for (const part of parts) {
        if (part.trim() === "") continue;
        let value: unknown;
        try {
          value = JSON.parse(part);
        } catch {
          // A SyntaxError's message quotes an excerpt of the line that did
          // not parse -- foreign bytes (a proxy's HTML error page, say) that
          // the page's error state would otherwise render verbatim. The
          // stream is scrubbed server-side; this is the one place a message
          // that is not the server's could still reach the page.
          throw new ApiError(
            response.status,
            "the server answered with something that was not JSON",
          );
        }
        onValue(value);
      }
    }
  } catch (caught) {
    if (signal.aborted) return;
    throw caught;
  } finally {
    reader.releaseLock();
  }
}

async function errorMessage(response: Response): Promise<string> {
  return (await errorBody(response)).message;
}

async function errorBody(
  response: Response,
): Promise<{ message: string; detail: unknown }> {
  const fallback = `request failed with ${response.status}`;
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") {
      return { message: body.detail, detail: body.detail };
    }
    if (body && body.detail !== undefined) {
      // A structured detail (FastAPI's validation list, or the config
      // editor's) has no single sentence in it; the caller unpacks it.
      return { message: fallback, detail: body.detail };
    }
  } catch {
    // A non-JSON error body (a proxy's HTML 502, say) is not worth surfacing
    // verbatim to the user.
  }
  return { message: fallback, detail: undefined };
}
