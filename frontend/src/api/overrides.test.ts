import { describe, expect, it } from "vitest";

import { fieldErrors } from "./overrides";

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
});
