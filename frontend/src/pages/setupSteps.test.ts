import { describe, expect, it } from "vitest";

import { farthestStep, stepAfter, visibleSteps, type StepId } from "./setupSteps";
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

describe("stepAfter", () => {
  it("is read from the progress the submit produced, not the one before it", () => {
    // The password pane's own case, and why this takes a progress at all: before
    // the submit the wizard knows only ["password"], so a next step computed
    // from THAT order can only clamp to the pane it is already on.
    expect(stepAfter("password", null)).toBe<StepId>("password");
    expect(stepAfter("password", BASE)).toBe<StepId>("url");
  });

  it("skips a step this deployment does not have", () => {
    expect(stepAfter("url", { ...BASE, public_url: true })).toBe<StepId>("database");
    expect(stepAfter("url", { ...BASE, public_url: true, database: true })).toBe<StepId>(
      "systems",
    );
  });

  it("advances past a step that left the order by being completed", () => {
    // The database step is dropped the moment a URL resolves -- which is what
    // its own submit makes true -- so the step just finished is not in the
    // post-submit order at all, and looking for it there finds nothing.
    expect(stepAfter("database", { ...BASE, public_url: true, database: true })).toBe<StepId>(
      "systems",
    );
  });

  it("stops at the last step", () => {
    expect(
      stepAfter("finish", {
        ...BASE,
        public_url: true,
        database: true,
        required: [],
        config_source: "state",
      }),
    ).toBe<StepId>("finish");
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
