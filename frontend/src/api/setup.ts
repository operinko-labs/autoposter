/** The setup wizard's own client.
 *
 * Separate from api/client.ts on purpose. The wizard's credential is a
 * memory-only setup token in an `X-Setup-Token` header, not the session
 * bearer, and it must never reach sessionStorage: the token dies with the
 * server process at the end of the wizard, and a stored copy would outlive it
 * as a value that looks valid and is not.
 *
 * Every refusal this API makes is a fixed sentence or an exception class name
 * -- it never echoes a value it was given -- which is what lets the pages
 * render `detail` verbatim instead of translating status codes.
 */
import { ApiError } from "./client";

export interface SetupState {
  setup: boolean;
  password_set?: boolean;
}

export interface SetupProgress {
  password: boolean;
  database: boolean;
  /** Environment variable name -> `"***REDACTED***"` when stored, `null` when
   * not. Never a value. */
  providers: Record<string, string | null>;
  /** The hard names still unresolved. The server owns this: several provider
   * credentials are legitimately optional forever, so "every field filled" is
   * not the test for whether the step is done. */
  required: string[];
  config: boolean;
  /** Where the document the NEXT BOOT will read comes from, or `null` while
   * none resolves: `"configured"` (an env-set path, e.g. a mounted ConfigMap)
   * or `"state"` (staged for the finish step to write). A word, never a path
   * -- this is a presence surface, not a filesystem browser. The wizard
   * offers the configuration step only while this is `null` (facts
   * Amendment 6): a document that already resolves cannot be replaced by this
   * step, so asking for a Plex URL a second time would write a file the next
   * boot never reads. */
  config_source: "configured" | "state" | null;
  /** Whether the wizard holds this deployment's own externally reachable
   * address (v2 step 2). Presence, never the value: /progress is a presence
   * surface for every line it serves. */
  public_url: boolean;
}

let setupToken: string | null = null;

function setSetupToken(next: string | null): void {
  setupToken = next;
}

async function setupFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (setupToken !== null) headers.set("X-Setup-Token", setupToken);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, { ...init, headers });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail;
    } catch {
      detail = undefined;
    }
    throw new ApiError(
      response.status,
      typeof detail === "string" ? detail : `request failed with ${response.status}`,
      detail,
    );
  }

  return (await response.json()) as T;
}

export function fetchSetupState(init: RequestInit = {}): Promise<SetupState> {
  return setupFetch<SetupState>("/api/setup/state", init);
}

export async function submitMasterPassword(password: string): Promise<void> {
  const body = await setupFetch<{ token: string }>("/api/setup/password", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  setSetupToken(body.token);
}

export function fetchSetupProgress(): Promise<SetupProgress> {
  return setupFetch<SetupProgress>("/api/setup/progress");
}

export function submitDatabaseUrl(url: string): Promise<{ ok: boolean }> {
  return setupFetch("/api/setup/database", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function submitPublicUrl(url: string): Promise<{ ok: boolean }> {
  return setupFetch("/api/setup/public-url", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export interface ProviderKeysResult {
  providers: Record<string, string | null>;
  /** The one value this API ever serves, and only on the call that generated
   * it (facts Amendment 5): the wizard-chosen webhook secret, once, never
   * again -- `null` on every later call, including the plain GET. */
  webhook_secret: string | null;
}

export function submitProviderKeys(
  values: Record<string, string>,
): Promise<ProviderKeysResult> {
  return setupFetch("/api/setup/providers", {
    method: "POST",
    body: JSON.stringify({ values }),
  });
}

export function submitPlexUrl(plexUrl: string): Promise<{ path: string }> {
  return setupFetch("/api/setup/config", {
    method: "POST",
    body: JSON.stringify({ plex_url: plexUrl }),
  });
}

export function finishSetup(): Promise<{ restarting: boolean }> {
  return setupFetch("/api/setup/finish", { method: "POST" });
}
