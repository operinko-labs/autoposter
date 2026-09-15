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
 * The document is the whole configuration, nested the way the config is: the
 * store holds it rather than a set of deltas over a mounted file, and every
 * page that writes sends all of it back. Two consequences drive the helpers
 * below.
 *
 *   - It is built from the whole served config, minus the keys that are not
 *     settings. Rebuilding a subset would delete every setting the page did
 *     not name, because there is no file layer left to fall back on.
 *   - Removing a key does not write `null` in its place. `null` is a value
 *     the API validates like any other, and a null-valued setting is almost
 *     always invalid -- so a control that wrote null could not clear.
 */
import { ApiError } from "./client";
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

/** Keys the enriched GET adds that are provenance, not configuration.
 *
 * Two readers and one list. `documentFromConfig` below drops them so the
 * document a page saves carries settings and nothing else, and the settings
 * page skips them so they are never rendered as rows -- either one alone would
 * offer the operator an edit the API is bound to reject.
 *
 * `secrets` is on the list and is not provenance in the same sense: it is the
 * separate `Secrets` model, and `merge_overrides` refuses the key outright at
 * any depth, so a document carrying it is a guaranteed 422. */
export const PROVENANCE_KEYS = [
  "frozen_paths",
  "redacted_paths",
  "keep_sentinel",
  "field_descriptions",
  "computed_paths",
  "live_paths",
  "overrides_revision",
  "restart_paths",
  "secrets",
];

/** The document as the store holds it: the whole served configuration, minus
 * the keys above, with the keep sentinel at every redacted path.
 *
 * This inverted when the store became the document. It used to be a DELTA
 * rebuilt from the paths the response said were overridden, and round-tripping
 * the whole config was the hazard its docstring warned about -- it would have
 * frozen today's file values as permanent overrides. There is no file layer
 * under the document any more: the whole configuration IS what is stored, so
 * rebuilding a delta would drop every setting the operator never touched, on
 * the very first save.
 *
 * A redacted path is the one place the served value is not the stored one.
 * `notifications.url` arrives as a bare host, so sending it back would store
 * that host and destroy the push token -- and leaving the path out would drop
 * the setting instead. Neither is recoverable from what the page was given, so
 * the server's keep sentinel goes in and the server, which still has the
 * stored value, resolves it. */
export function documentFromConfig(config: ConfigResponse): OverridesDocument {
  const keep = keepContract(config);
  let document: OverridesDocument = {};
  for (const [key, value] of Object.entries(config)) {
    if (PROVENANCE_KEYS.includes(key)) continue;
    document = { ...document, [key]: value };
  }
  for (const path of keep.paths) {
    // No rule of its own, deliberately: `keep.paths` is what THIS response
    // says it redacted, so the sentinel goes at exactly those paths and
    // nowhere else. A copy of the server's predicate here would be a fourth
    // thing that has to agree about the same set, and it cannot: the page
    // cannot tell a served `""` that is the stored value from a served `""`
    // the reduction produced out of a value it could not parse, so it would
    // send that truncation back and store it over the real setting. The
    // judgement lives on the one side holding both values.
    if (readPath(document, path) === undefined) continue;
    document = withPath(document, path, keep.sentinel);
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
  // (last-writer-wins used to collapse every earlier message for a field).
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

/** The sentence to show for a refused write, whatever shape it arrived in.
 *
 * Three shapes reach a page that writes the configuration, and only one of
 * them survives `errorBody`'s flattening as a sentence:
 *
 *   - a list -- the drop cap (`detail: [{path, message}]`) and every 422 the
 *     validator produces. `errorBody` reports `request failed with 422` for
 *     those, so a page reading `Error.message` would replace "send confirm:
 *     true to do it deliberately" with a status code, hiding the one sentence
 *     that says what to do next;
 *   - an object -- the stale-revision 409 (`{message, current_revision,
 *     changed_paths}`), which has the same problem;
 *   - a plain string, which `errorBody` already promotes to the message.
 *
 * Shared rather than written per panel for the reason the rest of this module
 * is shared: the panel that got it wrong would be the one whose refusal
 * mattered most. */
export function refusalMessage(caught: unknown): string {
  if (caught instanceof ApiError) {
    const messages = Object.entries(fieldErrors(caught.detail)).map(
      ([path, message]) =>
        // `document` is the whole body rather than a field the operator can
        // look at, so naming it would be noise in front of the sentence.
        path === "" || path === "document" ? message : `${path}: ${message}`,
    );
    if (messages.length > 0) return messages.join("; ");
    if (typeof caught.detail === "string") return caught.detail;
    if (isPlainObject(caught.detail) && typeof caught.detail.message === "string") {
      return caught.detail.message;
    }
  }
  return (caught as Error).message;
}

/** The revision the server served with this seed, or null when it served none.
 *
 * Null rather than a throw for a response that predates the field: an older
 * deployment's `GET /api/config` is a perfectly usable seed, and a page that
 * refused to save against one would be a worse failure than the one this
 * guards. `saveBody` then omits the key and the endpoint proceeds. */
export function revisionFromConfig(config: ConfigResponse): string | null {
  const revision = config.overrides_revision;
  return typeof revision === "string" && revision !== "" ? revision : null;
}

/** The body every writing page sends, built in exactly one place.
 *
 * The four pages that write this document each seed the WHOLE of it at mount
 * and PUT the whole result, so each of them can silently delete what the other
 * three saved. The seed's revision is what makes that a 409 instead. Built
 * here rather than per page for the reason this file exists at all: the page
 * that copied the first one badly is the page that would leave the key off and
 * put its operator straight back in the 2026-09-01 incident. */
export function saveBody(
  document: OverridesDocument,
  revision: string | null,
): string {
  return JSON.stringify(
    revision === null ? { document } : { document, expected_revision: revision },
  );
}

/** What the operator is told when the server refuses a stale save.
 *
 * Three facts and no instruction to retry: what happened, that nothing was
 * stored, and that the page in front of them is now showing the real settings.
 * An automatic retry would re-apply their edit onto a document they have never
 * seen, which is the same lost update wearing a friendlier face. */
export const STALE_SAVE_NOTE =
  "These settings were changed somewhere else while this page was open, so " +
  "nothing was saved. The page now shows the current settings — make your " +
  "change again.";

/** What a panel says when its save changed something a restart applies.
 *
 * A pointer, never a second list. What a save response reports is its own
 * difference against the generation the process is running; what the settings
 * page's banner renders is the store's list, measured against what the process
 * booted on and emptied by the boot that settles it. The two disagree the
 * first time a setting is edited and put back, and only one of them is beside
 * the button that does something about it. Shared here for the reason every
 * other sentence in this module is: four panels saying it four ways is four
 * chances for one of them to go stale. */
export const RESTART_NOTE =
  "Settings that need a restart are listed in the banner on the Settings page.";
