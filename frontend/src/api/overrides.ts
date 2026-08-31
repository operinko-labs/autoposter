/** The overrides document, and the four operations every page that writes one
 * performs on it.
 *
 * These lived in `pages/Settings.tsx` while that page was the only writer.
 * The catalog picker is the second, and it writes through the same three
 * endpoints with the same document -- so the helpers moved here rather than
 * being copied. A copy is the shape this particular bug takes: the seeding
 * rule below (a redacted path seeds the server's keep sentinel, never the
 * served value) is not something a second page would reproduce by reading the
 * first, and the page that got it wrong would destroy a push token on a save
 * about something else entirely.
 *
 * The document is a *delta*: it holds only the fields the operator changed,
 * nested the way the config is. Two consequences drive every helper below.
 *
 *   - It is never built from the whole config. Round-tripping the config
 *     would store today's values as overrides, freezing them against every
 *     future change to the git-owned YAML.
 *   - Reverting a field is its key going *away*. `null` is a value the API
 *     validates like any other, and a null-valued setting is almost always
 *     invalid -- so a clear control that wrote null could not clear.
 */
import type { ConfigResponse, OverridesDocument } from "./types";

export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function hasPath(document: OverridesDocument, path: string): boolean {
  const [head, ...rest] = path.split(".");
  if (!(head in document)) return false;
  if (rest.length === 0) return true;
  const child = document[head];
  return isPlainObject(child) && hasPath(child, rest.join("."));
}

export function readPath(source: Record<string, unknown>, path: string): unknown {
  const [head, ...rest] = path.split(".");
  const value = source[head];
  if (rest.length === 0) return value;
  return isPlainObject(value) ? readPath(value, rest.join(".")) : undefined;
}

export function withPath(
  document: OverridesDocument,
  path: string,
  value: unknown,
): OverridesDocument {
  const [head, ...rest] = path.split(".");
  if (rest.length === 0) return { ...document, [head]: value };
  const child = document[head];
  return {
    ...document,
    [head]: withPath(isPlainObject(child) ? child : {}, rest.join("."), value),
  };
}

export function withoutPath(
  document: OverridesDocument,
  path: string,
): OverridesDocument {
  const [head, ...rest] = path.split(".");
  if (!(head in document)) return document;
  const dropHead = () => {
    const kept = { ...document };
    delete kept[head];
    return kept;
  };
  if (rest.length === 0) return dropHead();
  const child = document[head];
  if (!isPlainObject(child)) return document;
  const pruned = withoutPath(child, rest.join("."));
  // An emptied branch is not a change; leaving `{}` behind would send a
  // section the operator just cleared.
  return Object.keys(pruned).length === 0
    ? dropHead()
    : { ...document, [head]: pruned };
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/** The redacted paths this response is usable for, and the marker to send for
 * them -- both, or neither.
 *
 * A response naming redacted paths but carrying no marker cannot be seeded
 * either way (see `documentFromConfig`), so it is treated as a response that
 * redacted nothing, which is exactly how the settings page behaved before the
 * marker existed. */
export function keepContract(
  config: ConfigResponse,
): { paths: string[]; sentinel: string } {
  const sentinel = config.keep_sentinel;
  if (typeof sentinel !== "string" || sentinel === "") {
    return { paths: [], sentinel: "" };
  }
  return { paths: stringList(config.redacted_paths), sentinel };
}

/** The document as the server currently holds it, rebuilt from the paths it
 * says are overridden and the values it is serving for them (an override
 * wins the merge, so the served value *is* the stored one). Without this, a
 * save of one field would drop every override the operator saved earlier.
 *
 * Except at a redacted path, where the served value is *not* the stored one.
 * `notifications.url` arrives as a bare host, so seeding it would re-submit
 * that host as the override and destroy the push token on the next unrelated
 * save -- and skipping it would drop the override instead. Neither is
 * recoverable from what the page was given, so the server's keep sentinel
 * goes in and the server, which still has the stored value, resolves it. */
export function documentFromConfig(config: ConfigResponse): OverridesDocument {
  const paths = config.overridden_paths;
  if (!Array.isArray(paths)) return {};
  const keep = keepContract(config);
  let document: OverridesDocument = {};
  for (const path of paths) {
    if (typeof path !== "string") continue;
    if (keep.paths.includes(path)) {
      document = withPath(document, path, keep.sentinel);
      continue;
    }
    const value = readPath(config, path);
    if (value === undefined) continue;
    document = withPath(document, path, value);
  }
  return document;
}

/** A 422's `detail` flattened to path -> message, with several messages on one
 * path joined by `"; "` into that path's single string.
 *
 * The separator is part of the contract, not an implementation detail:
 * `refusalMessage` joins the map's *values* with the same `"; "`, so a
 * multi-message path composes into that sentence rather than nesting inside it.
 *
 * Two shapes arrive through one endpoint: the handler's own `{path, message}`
 * entries, and FastAPI's `{loc, msg}` when the request validator rejects the
 * body before the handler runs. `loc` is a segment list rooted at the request
 * body, so the field's own path is what follows "document". */
export function fieldErrors(detail: unknown): Record<string, string> {
  if (!Array.isArray(detail)) return {};
  const errors: Record<string, string> = {};
  // Joined, not last-writer-wins: two entries on one path both survive
  // (ccui review M6 -- the collapse hid every earlier message for a field).
  const add = (path: string, message: string) => {
    // `hasOwn`, not `in`: `errors` is a literal, so `in` would find
    // `constructor`/`toString` on the prototype and join onto them.
    errors[path] = Object.hasOwn(errors, path)
      ? `${errors[path]}; ${message}`
      : message;
  };
  for (const entry of detail) {
    if (!isPlainObject(entry)) continue;
    if (typeof entry.path === "string") {
      add(entry.path, String(entry.message ?? "invalid value"));
    } else if (Array.isArray(entry.loc)) {
      const segments = entry.loc.map(String);
      const start = segments.indexOf("document");
      const path = (start === -1 ? segments : segments.slice(start + 1)).join(".");
      add(path, String(entry.msg ?? "invalid value"));
    }
  }
  return errors;
}
