import { describe, expect, it } from "vitest";

import { canNavigate, farthestStep, stepAfter, visibleSteps, type StepId } from "./setupSteps";
import type { SetupProgress } from "../api/setup";

const BASE: SetupProgress = {
  password: true,
  database: false,
  database_source: "missing",
  providers: { AUTOPOSTER_PLEX_TOKEN: null },
  required: ["AUTOPOSTER_PLEX_TOKEN"],
  config: false,
  config_source: null,
  public_url: false,
  checked_systems: [],
};

describe("visibleSteps", () => {
  it("hides the database step only for a value the boot resolver already holds", () => {
    // The boolean cannot decide this on its own: the step's OWN submit makes
    // `database` true, so keying on it deleted the step the moment it was
    // answered -- Back landed on the address pane and the step's Stored pill
    // and empty-means-keep were unreachable for the life of the process.
    expect(visibleSteps({ ...BASE, database: false, database_source: "missing" })).toContain<StepId>(
      "database",
    );
    expect(
      visibleSteps({ ...BASE, database: true, database_source: "staged" }),
    ).toContain<StepId>("database");
    expect(
      visibleSteps({ ...BASE, database: true, database_source: "resolved" }),
    ).not.toContain<StepId>("database");
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
    expect(
      stepAfter("url", { ...BASE, public_url: true, database: true, database_source: "resolved" }),
    ).toBe<StepId>("systems");
  });

  it("advances past a step that is not in the order at all", () => {
    // No step leaves the order by being answered any more -- a staged one
    // stays -- but the search-forward shape is what makes that true of a step
    // the SERVER drops mid-flow as well, and it is cheaper than a walk that
    // has to be right about the order twice.
    expect(
      stepAfter("database", {
        ...BASE,
        public_url: true,
        database: true,
        database_source: "resolved",
      }),
    ).toBe<StepId>("systems");
  });

  it("stops at the last step", () => {
    expect(
      stepAfter("finish", {
        ...BASE,
        public_url: true,
        database: true,
        database_source: "staged",
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

  it("counts a document the wizard staged as the config step answered", () => {
    expect(
      farthestStep({
        ...BASE,
        public_url: true,
        database: true,
        required: [],
        config_source: "staged",
      }),
    ).toBe<StepId>("finish");
  });
});

describe("canNavigate", () => {
  it("refuses a step this deployment does not have at all", () => {
    // `indexOf` answers -1 for a step outside the order, and -1 is <= every
    // index, so the untightened comparison called an absent step reachable.
    // Only the page's own `order.includes` beside it kept that off screen.
    const resolved: SetupProgress = {
      ...BASE,
      public_url: true,
      database: true,
      database_source: "resolved",
    };
    expect(canNavigate("database", resolved)).toBe(false);
    expect(canNavigate("url", resolved)).toBe(true);
  });
});
