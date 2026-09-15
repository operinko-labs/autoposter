/** The Servers tab's client: one server's row, its two live reads, its two
 * writes, its credential, and its catch-up.
 *
 * Every function here is one route in `api/servers.py`, and the shapes are
 * that module's own. Three of its rules are visible in the types rather than
 * left to each caller to remember, because each one is a refusal a card would
 * otherwise walk into:
 *
 *  - `ProbeBody` is BOTH fields or NEITHER. An address supplied with the
 *    request must come with the credential to use against it; this service
 *    does not send a credential it holds to an address a request named. A
 *    body with neither field uses the address and the credential the
 *    deployment booted with.
 *  - `expected_revision` is required on both writes and on the library map,
 *    where the settings body allows it to be left out: those routes read the
 *    stored document outside the lock they commit under, so the revision is
 *    what turns a concurrent settings save into a refusal rather than a
 *    silent revert of it.
 *  - `excluded_libraries` is required on a save and is a REPLACEMENT, while
 *    `switches` is a delta over the stored block. An omitted list would empty
 *    that server's exclusions with a 200 and no mention of it.
 *
 * A removal always sends a body, even when both its fields are the defaults:
 * the route declares one, so a bodyless DELETE is refused by the request
 * validator before the handler runs.
 */
import { apiFetch } from "./client";
import type { SecretSource } from "./secrets";

export interface ServerHealth {
  /** null when nothing has checked yet -- a deployment whose liveness poller
   * has not run. Rendered as "not checked", never as "down". */
  ok: boolean | null;
  detail: string | null;
  checked_at: string | null;
}

export interface ServerRow {
  name: string;
  configured: boolean;
  url: string | null;
  excluded_libraries: string[];
  credential_source: SecretSource;
  /** Whether this server's saved block differs from the one the process
   * booted with. True from a save until the restart that applies it, and it
   * is why a connection check made in that window reaches the old address
   * unless a new one is typed into the card. */
  restart_pending: boolean;
  health: ServerHealth;
}

export interface LibraryRow {
  id: string;
  name: string;
  kind: string;
}

export interface CheckResult {
  ok: boolean;
  refused: boolean;
  failure: string | null;
  /** The server's own version string when it volunteered one, so the pill can
   * read "connected, version ...". Null whenever it did not. */
  version: string | null;
  detail: string;
}

/** One catch-up run and what is left of it. */
export interface CatchUpRun {
  run_id: number;
  server: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  cadence_seconds: number | null;
  detail: string | null;
  due: number;
  done: number;
  failed: number;
  total: number;
}

/** What `GET /api/servers/{name}/catch-up` answers: the run, or the explicit
 * envelope the route sends instead of a bare null, so a card can tell "no run
 * yet" from a read that failed without reading the status code. */
export type CatchUpProgress = CatchUpRun | { run: null };

/** The run inside a progress answer, or null for the empty envelope.
 *
 * A function rather than a comparison at each call site: the two arms of the
 * union are told apart by a key, and a card that tested the wrong one would
 * render "nothing has run" over a run that is in flight. */
export function catchUpRun(progress: CatchUpProgress | null): CatchUpRun | null {
  return progress !== null && "run_id" in progress ? progress : null;
}

/** What starting one answers. Not the progress: the counts come from the read
 * beside this, which is the one place they are computed. */
export interface CatchUpStarted {
  run_id: number;
  server: string;
  cadence_seconds: number | null;
}

export interface CatchUpCancelled {
  run_id: number;
  restored: number;
  removed: number;
  detail: string;
}

export interface RetryResult {
  server: string;
  artwork: number;
  metadata: number;
}

export interface ProbeBody {
  url?: string;
  credential_value?: string;
}

export interface ServerSaveBody {
  url: string;
  excluded_libraries: string[];
  switches: Record<string, boolean>;
  expected_revision: string;
  confirm: boolean;
}

export interface ServerRemovalBody {
  expected_revision: string;
  confirm: boolean;
}

/** The three fields of a configuration write's answer this tab reads. The
 * response carries more -- the drop report and the new revision -- and those
 * belong to the settings page, which is what re-reads after a card writes. */
export interface ConfigWriteResult {
  version_before: string;
  version_after: string;
  restart_required: string[];
}

export interface ServerRemovalResult extends ConfigWriteResult {
  /** False when the block was removed but the stored credential survived it.
   * The removal still happened, so the card says the credential is still
   * stored rather than inviting a retry of a removal already made. */
  credential_cleared: boolean;
}

export interface CredentialResult {
  name: string;
  credential_source: SecretSource;
  /** A clear only. True when the layer taking over is one this process cannot
   * read back until it restarts. */
  restart_required?: boolean;
}

function at(name: string): string {
  return `/api/servers/${encodeURIComponent(name)}`;
}

export async function fetchServers(): Promise<ServerRow[]> {
  const body = await apiFetch<{ servers?: ServerRow[] }>("/api/servers");
  // Shape-checked rather than trusted, the way the secrets listing is: this
  // tab is how a server is added, so a deployment answering something else
  // should leave the tab empty rather than take the page down.
  return Array.isArray(body.servers) ? body.servers : [];
}

export function checkServer(name: string, body: ProbeBody): Promise<CheckResult> {
  return apiFetch<CheckResult>(`${at(name)}/check`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function fetchLibraries(
  name: string,
  body: ProbeBody,
): Promise<LibraryRow[]> {
  const answer = await apiFetch<{ libraries?: LibraryRow[] }>(
    `${at(name)}/libraries`,
    { method: "POST", body: JSON.stringify(body) },
  );
  // Guarded for the listing's reason and one more: the card maps over this
  // list inside its render, so an answer without the key would throw where
  // the card's own error line cannot catch it, taking the tab down with it.
  return Array.isArray(answer.libraries) ? answer.libraries : [];
}

export function saveServer(
  name: string,
  body: ServerSaveBody,
): Promise<ConfigWriteResult> {
  return apiFetch<ConfigWriteResult>(at(name), {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function removeServer(
  name: string,
  body: ServerRemovalBody,
): Promise<ServerRemovalResult> {
  return apiFetch<ServerRemovalResult>(at(name), {
    method: "DELETE",
    body: JSON.stringify(body),
  });
}

export function setServerCredential(
  name: string,
  value: string,
): Promise<CredentialResult> {
  return apiFetch<CredentialResult>(`${at(name)}/credential`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

export function clearServerCredential(name: string): Promise<CredentialResult> {
  return apiFetch<CredentialResult>(`${at(name)}/credential`, {
    method: "DELETE",
  });
}

export function startCatchUp(name: string): Promise<CatchUpStarted> {
  return apiFetch<CatchUpStarted>(`${at(name)}/catch-up`, { method: "POST" });
}

export function fetchCatchUp(name: string): Promise<CatchUpProgress> {
  return apiFetch<CatchUpProgress>(`${at(name)}/catch-up`);
}

export function cancelCatchUp(name: string): Promise<CatchUpCancelled> {
  return apiFetch<CatchUpCancelled>(`${at(name)}/catch-up`, { method: "DELETE" });
}

export function retryFailed(name: string): Promise<RetryResult> {
  return apiFetch<RetryResult>(`${at(name)}/retry-failed`, { method: "POST" });
}

/** The whole map, not a patch: the editor sends every pair it shows, and the
 * route stores only the pairs whose two names differ. */
export function saveLibraryMap(
  pairs: Record<string, string>,
  revision: string,
  confirm = false,
): Promise<ConfigWriteResult> {
  return apiFetch<ConfigWriteResult>("/api/servers/jellyfin/library-map", {
    method: "PUT",
    body: JSON.stringify({ pairs, expected_revision: revision, confirm }),
  });
}
