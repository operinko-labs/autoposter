import { describe, expect, it } from "vitest";

import { canNavigate, farthestStep, stepAfter, visibleSteps, type StepId } from "./setupSteps";
import type { SetupProgress } from "../api/setup";

/** Neither server set up -- what a fresh deployment answers with. The two
 * server credentials are not in `providers` at all: each is required exactly
 * when ITS server is configured, so this line is the only place either one's
 * presence is reported. */
const NO_SERVERS = {
  plex: { configured: false, credential: false, checked: false },
  jellyfin: { configured: false, credential: false, checked: false },
};

/** One server complete, which is all the media-server step asks for: a
 * deployment runs on Plex, on Jellyfin, or on both. */
const PLEX_READY = {
  ...NO_SERVERS,
  plex: { configured: true, credential: true, checked: false },
};

const BASE: SetupProgress = {
  password: true,
  database: false,
  database_source: "missing",
  providers: { AUTOPOSTER_TMDB_TOKEN: null },
  required: ["AUTOPOSTER_TMDB_TOKEN"],
  config: false,
  config_source: null,
  public_url: false,
  checked_systems: [],
  servers: NO_SERVERS,
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
    // The address is asked for on every deployment shape -- the
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
    ).toBe<StepId>("servers");
  });

  it("puts the media-server step between the database and the systems step", () => {
    // The order the wizard walks: a deployment's media server is asked for
    // before the provider credentials, because the configuration document the
    // servers step writes is what every later step's deployment shape is read
    // from.
    expect(stepAfter("database", { ...BASE, public_url: true, database: true })).toBe<StepId>(
      "servers",
    );
    expect(stepAfter("servers", { ...BASE, public_url: true, database: true })).toBe<StepId>(
      "systems",
    );
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
    ).toBe<StepId>("servers");
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
    expect(farthestStep({ ...BASE, public_url: true, database: true })).toBe<StepId>("servers");
    expect(
      farthestStep({ ...BASE, public_url: true, database: true, servers: PLEX_READY }),
    ).toBe<StepId>("systems");
    expect(
      farthestStep({
        ...BASE,
        public_url: true,
        database: true,
        servers: PLEX_READY,
        required: [],
        config_source: "state",
      }),
    ).toBe<StepId>("finish");
  });

  it("gates systems behind at least one configured server with a credential", () => {
    // The step gate is `missing_server_setup`'s own rule, reported: EITHER
    // server finishes the step, and neither half alone does. An address with
    // no credential is a deployment the next boot refuses; a credential with
    // no address is a token for a server nothing will talk to.
    const ready = { ...BASE, public_url: true, database: true };
    expect(farthestStep(ready)).toBe<StepId>("servers");
    expect(
      farthestStep({
        ...ready,
        servers: { ...NO_SERVERS, jellyfin: { configured: true, credential: false, checked: false } },
      }),
    ).toBe<StepId>("servers");
    expect(
      farthestStep({
        ...ready,
        servers: { ...NO_SERVERS, jellyfin: { configured: false, credential: true, checked: false } },
      }),
    ).toBe<StepId>("servers");
    // Jellyfin alone is a finished step: the example's `plex:` block is not a
    // requirement this deployment inherits.
    expect(
      farthestStep({
        ...ready,
        servers: { ...NO_SERVERS, jellyfin: { configured: true, credential: true, checked: false } },
      }),
    ).toBe<StepId>("systems");
  });

  it("keeps the step unmet while ANY configured server is missing its credential", () => {
    // The gate's SECOND clause, and the one a "at least one server is done"
    // reading gets wrong: `missing_server_setup` demands the credential of
    // every server the document names, so a `plex:` block with no token is
    // refused at the finish however complete Jellyfin is beside it. A page
    // that waved this through would disagree with the step it leads to.
    expect(
      farthestStep({
        ...BASE,
        public_url: true,
        database: true,
        required: [],
        config_source: "state",
        servers: {
          plex: { configured: true, credential: false, checked: false },
          jellyfin: { configured: true, credential: true, checked: true },
        },
      }),
    ).toBe<StepId>("servers");
  });

  it("never asks for a credential for a server this deployment does not run", () => {
    // `checked` is a SESSION fact and false after a reload, so it must not
    // gate anything: a wizard picked up in a second tab would be sent back to
    // a step it had already finished.
    expect(
      farthestStep({
        ...BASE,
        public_url: true,
        database: true,
        servers: { ...NO_SERVERS, plex: { configured: true, credential: true, checked: false } },
      }),
    ).toBe<StepId>("systems");
  });

  it("counts a document the wizard staged as the config step answered", () => {
    expect(
      farthestStep({
        ...BASE,
        public_url: true,
        database: true,
        servers: PLEX_READY,
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
