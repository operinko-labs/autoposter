import { describe, expect, it } from "vitest";

import {
  documentFromConfig,
  fieldErrors,
  PROVENANCE_KEYS,
  revisionFromConfig,
  saveBody,
  STALE_SAVE_NOTE,
} from "./overrides";

/** The store holds the whole configuration, so the document a page saves is
 * the whole configuration. A seed that rebuilt a subset would delete every
 * setting it did not name -- there is no file layer left to fall back on. */
describe("documentFromConfig", () => {
  const CONFIG = {
    workers: 5,
    plex: { url: "http://plex:32400", excluded_libraries: ["Photos"] },
    notifications: { enabled: true, url: "kuma.example.com" },
    secrets: { plex_token: "***REDACTED***" },
    frozen_paths: { workers: "sized at startup" },
    redacted_paths: ["notifications.url"],
    keep_sentinel: "***KEEP***",
    field_descriptions: { workers: "How many render workers run." },
    computed_paths: ["version"],
    live_paths: [],
    overrides_revision: "rev-1",
    restart_paths: [],
  };

  it("carries every setting the response served", () => {
    const document = documentFromConfig(CONFIG);

    expect(document.workers).toBe(5);
    expect(document.plex).toEqual({
      url: "http://plex:32400",
      excluded_libraries: ["Photos"],
    });
  });

  it("drops the keys that are not settings, secrets included", () => {
    const document = documentFromConfig(CONFIG);

    for (const key of PROVENANCE_KEYS) {
      expect(document[key]).toBeUndefined();
    }
  });

  it("sends the keep marker at a redacted path, never the truncated value", () => {
    // The served host is not the stored URL: storing it would destroy the push
    // token embedded in the real one, and leaving the path out would drop the
    // setting. Neither is recoverable from what the page was given.
    const document = documentFromConfig(CONFIG) as {
      notifications: { enabled: boolean; url: string };
    };

    expect(document.notifications.url).toBe("***KEEP***");
    expect(document.notifications.enabled).toBe(true);
    expect(JSON.stringify(document)).not.toContain("kuma.example.com");
  });

  it("leaves a redacted path alone when the response never served it", () => {
    const { notifications: _dropped, ...without } = CONFIG;
    const document = documentFromConfig(without);

    expect(document.notifications).toBeUndefined();
  });

  it("treats a response with no marker as one that redacted nothing", () => {
    // Such a response cannot be seeded either way, so the page behaves as it
    // did before the marker existed rather than inventing one.
    const document = documentFromConfig({
      ...CONFIG,
      keep_sentinel: undefined,
    }) as { notifications: { url: string } };

    expect(document.notifications.url).toBe("kuma.example.com");
  });
});

/** Two 422 entries on the same path used to collapse to the LAST one --
 * last-writer-wins hid every earlier message for the field. Reachable for a
 * multi-error body on a single-field endpoint, and shared by all four panels
 * that render `fieldErrors`. */
describe("fieldErrors", () => {
  it("joins two errors on the same path instead of keeping only the last", () => {
    const detail = [
      { path: "collections.definitions", message: "title is a duplicate" },
      { path: "collections.definitions", message: "the separator prefix is reserved" },
    ];

    expect(fieldErrors(detail)).toEqual({
      "collections.definitions":
        "title is a duplicate; the separator prefix is reserved",
    });
  });

  it("joins across the two entry shapes too", () => {
    // One handler-shaped entry, one FastAPI-shaped entry, same field.
    const detail = [
      { path: "plex.url", message: "not a URL" },
      { loc: ["body", "document", "plex", "url"], msg: "value error" },
    ];

    expect(fieldErrors(detail)).toEqual({
      "plex.url": "not a URL; value error",
    });
  });

  it("does not treat an inherited property name as an already-seen path", () => {
    // `errors` is an object literal, so a membership test that walks the
    // prototype chain reports `constructor` as present on its first sighting
    // and prepends `Object`'s own source to the message.
    const detail = [{ path: "constructor", message: "not a config section" }];

    expect(fieldErrors(detail)).toEqual({ constructor: "not a config section" });
  });

  it("maps a handler-shaped entry with no input key to a field error", () => {
    // The app's own RequestValidationError handler serves {type, loc, msg} and
    // drops `input` (the operator's paste). `fieldErrors` never read `input`,
    // and this is what keeps it that way: the entry it gets is now strictly
    // smaller than FastAPI's default one.
    const detail = [
      {
        type: "missing",
        loc: ["body", "document", "plex", "url"],
        msg: "Field required",
      },
    ];

    expect(fieldErrors(detail)).toEqual({ "plex.url": "Field required" });
  });
});

describe("the revision a page carries with its seed", () => {
  it("reads the revision the config response served", () => {
    expect(revisionFromConfig({ overrides_revision: "abc" })).toBe("abc");
  });

  it("reads null from a response that has no revision, rather than throwing", () => {
    // A response from before the field existed. The save then omits the key
    // and the server proceeds -- the same posture the endpoint takes.
    expect(revisionFromConfig({})).toBeNull();
    expect(revisionFromConfig({ overrides_revision: "" })).toBeNull();
    expect(revisionFromConfig({ overrides_revision: 7 })).toBeNull();
  });

  it("sends the revision alongside the document, wrapped", () => {
    expect(JSON.parse(saveBody({ workers: 9 }, "abc"))).toEqual({
      document: { workers: 9 },
      expected_revision: "abc",
    });
  });

  it("omits the key entirely when there is no revision to send", () => {
    // Not `expected_revision: null` -- the body model types it as
    // `str | None` and null would read as "I have no seed", which is exactly
    // what it means, but omitting is what the four pages' tests assert and
    // what an older client sends. One shape, not two.
    const body = JSON.parse(saveBody({ workers: 9 }, null));
    expect(body).toEqual({ document: { workers: 9 } });
    expect("expected_revision" in body).toBe(false);
  });

  it("never tells the operator to retry", () => {
    // A retry would re-apply the edit onto a document they have not seen.
    expect(STALE_SAVE_NOTE).not.toMatch(/try again|retry/i);
    expect(STALE_SAVE_NOTE).toMatch(/nothing was saved/i);
  });
});
