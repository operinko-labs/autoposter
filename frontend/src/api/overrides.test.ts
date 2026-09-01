import { describe, expect, it } from "vitest";

import {
  fieldErrors,
  revisionFromConfig,
  saveBody,
  STALE_SAVE_NOTE,
} from "./overrides";

/** M6 (the ccui review, deferred cross-phase): two 422 entries on the same
 * path used to collapse to the LAST one -- last-writer-wins hid every earlier
 * message for the field. Reachable for a multi-error body on a single-field
 * endpoint, and shared by all four panels that render `fieldErrors`. */
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
