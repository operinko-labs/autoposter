/** The secrets accordion's client. Names and sources; never a value.
 *
 * There is no `getSecret`, and there is no place to add one: the server does
 * not serve a stored value back, by design. The listing answers where each
 * name's running value comes from, a set answers the new source, and a clear
 * answers the source that takes over -- which is the whole vocabulary the
 * page needs to describe a secret it is never allowed to show.
 */
import { apiFetch } from "./client";

/** One of the four layers a running value can come from, in precedence order:
 * the encrypted store, the state file, the process environment, or nothing. */
export type SecretSource = "stored" | "state file" | "environment" | "unset";

export interface SecretRow {
  name: string;
  source: SecretSource;
  /** True for the one credential this service mints rather than is given. */
  generated: boolean;
}

/** What a write answers: the name and where its value now comes from.
 *
 * A clear adds `restart_required`, which is not a detail the panel may
 * invent: the layer that takes over can be one this process can no longer
 * read (its own environment holds a copy of the row just removed), and only
 * the server knows which of the two happened.
 */
export interface SecretWriteResult {
  name: string;
  source: SecretSource;
  restart_required?: boolean;
}

export async function fetchSecrets(): Promise<SecretRow[]> {
  const body = await apiFetch<{ secrets?: SecretRow[] }>("/api/secrets");
  // Shape-checked rather than trusted, the way the settings page reads
  // `restart_paths`: this list is rendered by a panel that mounts whether or
  // not the rest of the page loaded, and a deployment that answers something
  // else should leave the accordion empty rather than take the page down.
  return Array.isArray(body.secrets) ? body.secrets : [];
}

export async function setSecret(
  name: string,
  value: string,
): Promise<SecretWriteResult> {
  return apiFetch<SecretWriteResult>(`/api/secrets/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

export async function clearSecret(name: string): Promise<SecretWriteResult> {
  return apiFetch<SecretWriteResult>(`/api/secrets/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}
