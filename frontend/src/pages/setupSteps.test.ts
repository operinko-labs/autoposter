import { describe, expect, it } from "vitest";

import { farthestStep, visibleSteps, type StepId } from "./setupSteps";
import type { SetupProgress } from "../api/setup";

const BASE: SetupProgress = {
  password: true,
  database: false,
  providers: { AUTOPOSTER_PLEX_TOKEN: null },
  required: ["AUTOPOSTER_PLEX_TOKEN"],
  config: false,
  config_source: null,
  public_url: false,
};

describe("visibleSteps", () => {
  it("offers the database step only while nothing resolves from the environment", () => {
    expect(visibleSteps({ ...BASE, database: false })).toContain<StepId>("database");
    expect(visibleSteps({ ...BASE, database: true })).not.toContain<StepId>("database");
  });

  it("is the password step alone before any token exists", () => {
    expect(visibleSteps(null)).toEqual<StepId[]>(["password"]);
  });

  it("keeps the URL step whether or not a document already resolves", () => {
    // Facts C1: the address is asked for on every deployment shape -- the
    // registration needs it even where it cannot be persisted.
    expect(visibleSteps({ ...BASE, config_source: "configured" })).toContain<StepId>("url");
  });
});

describe("farthestStep", () => {
  it("does not run ahead of the server", () => {
    expect(farthestStep({ ...BASE, public_url: false })).toBe<StepId>("url");
    expect(farthestStep({ ...BASE, public_url: true, database: false })).toBe<StepId>("database");
    expect(farthestStep({ ...BASE, public_url: true, database: true })).toBe<StepId>("systems");
    expect(
      farthestStep({ ...BASE, public_url: true, database: true, required: [], config_source: "state" }),
    ).toBe<StepId>("finish");
  });
});
