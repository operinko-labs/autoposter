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
 * Swapping the dashboard's polling for a WebSocket in phase 4c should touch
 * this module and nothing else.
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

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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
    throw new ApiError(response.status, await errorMessage(response));
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    // A non-JSON error body (a proxy's HTML 502, say) is not worth surfacing
    // verbatim to the user.
  }
  return `request failed with ${response.status}`;
}
