import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

/** Read off disk rather than imported as `?raw`.
 *
 * `css: false` in vite.config.ts stubs CSS modules out, and Vite classifies
 * `shell.css?raw` as CSS too, so the raw form is stubbed with it: the first
 * draft of this file imported the five stylesheets that way and every
 * assertion below was reading an empty string. That version failed loudly
 * (12 of 13 red) rather than passing vacuously, but only by luck -- a suite
 * asserting `.not.toContain` would have gone green on nothing at all.
 *
 * `node:fs` is typed in src/node-builtins.d.ts; see there for why. */
function stylesheet(relative: string): string {
  return readFileSync(new URL(relative, import.meta.url), "utf8");
}

const catalogCss = stylesheet("./pages/catalog.css");
const collectionsCss = stylesheet("./pages/collections.css");
const itemCss = stylesheet("./pages/item.css");
const logsCss = stylesheet("./pages/logs.css");
const mismatchesCss = stylesheet("./pages/mismatches.css");
const modesCss = stylesheet("./pages/modes.css");
const settingsCss = stylesheet("./pages/settings.css");
const shellCss = stylesheet("./shell/shell.css");
const testingCss = stylesheet("./pages/testing.css");
const themeCss = stylesheet("./theme.css");

/* The mobile sweep's findings are mostly layout, and this suite runs with
 * `css: false` in a jsdom that computes no layout at all -- `getComputedStyle`
 * would answer the initial value for every one of these properties whether the
 * rule shipped or not. So these read the stylesheets as text instead.
 *
 * That is a real guard and a narrow one: it fails if a declaration is deleted
 * or its value changed, and it cannot tell whether the result looks right in a
 * browser. The pixel half of each finding was checked by hand at 500px; what
 * is pinned here is that the rule is still in the file. */

interface Rule {
  selectors: string;
  declarations: string;
  /** The `@media` prelude this rule sits inside, or null at the top level. */
  media: string | null;
}

/** A deliberately small reader: these five stylesheets nest at most one
 * `@media` deep, so tracking a single at-rule level is enough, and a real CSS
 * parser would be a dependency bought for one test file. */
function parse(css: string): Rule[] {
  const source = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const rules: Rule[] = [];
  let media: string | null = null;
  let prelude = "";
  let index = 0;

  while (index < source.length) {
    const character = source[index];

    if (character === "{") {
      const head = prelude.trim();
      prelude = "";
      if (head.startsWith("@")) {
        media = head;
        index += 1;
        continue;
      }
      let depth = 1;
      let end = index + 1;
      while (end < source.length && depth > 0) {
        if (source[end] === "{") depth += 1;
        else if (source[end] === "}") depth -= 1;
        end += 1;
      }
      rules.push({ selectors: head, declarations: source.slice(index + 1, end - 1), media });
      index = end;
      continue;
    }

    if (character === "}") {
      media = null;
      prelude = "";
      index += 1;
      continue;
    }

    prelude += character;
    index += 1;
  }

  return rules;
}

/** The value a selector's rule gives a property, with whitespace collapsed.
 * Throws rather than returning undefined when the rule itself is missing, so
 * a deleted rule reads as "no rule for .x" instead of an equality failure
 * that looks like a value drift. */
function declaration(
  css: string,
  selector: string,
  property: string,
  media: string | null = null,
): string {
  const matching = parse(css).filter(
    (rule) =>
      rule.media === media &&
      rule.selectors.split(",").some((one) => one.trim() === selector),
  );
  if (matching.length === 0) {
    throw new Error(`no rule for ${selector}${media === null ? "" : ` inside ${media}`}`);
  }
  const declarations = matching.map((rule) => rule.declarations).join(";");
  let found: string | null = null;
  for (const entry of declarations.split(";")) {
    const colon = entry.indexOf(":");
    if (colon === -1) continue;
    if (entry.slice(0, colon).trim() !== property) continue;
    // Last one wins, as the cascade would.
    found = entry.slice(colon + 1).trim().replace(/\s+/g, " ");
  }
  if (found === null) {
    throw new Error(`${selector} declares no ${property}`);
  }
  return found;
}

const MOBILE = "@media (max-width: 640px)";

describe("the parser these assertions rest on", () => {
  it("reads a declaration, and separates a media rule from its top-level twin", () => {
    const css = "a { color: red; }\n@media print { a { color: blue; } }";
    expect(declaration(css, "a", "color")).toBe("red");
    expect(declaration(css, "a", "color", "@media print")).toBe("blue");
    expect(() => declaration(css, "b", "color")).toThrow("no rule for b");
    expect(() => declaration(css, "a", "width")).toThrow("declares no width");
  });
});

describe("wide content scrolls in its own container", () => {
  it("gives .table-scroll the horizontal scrollbar the four tables sit in", () => {
    expect(declaration(shellCss, ".table-scroll", "overflow-x")).toBe("auto");
  });

  it("keeps a timestamp on one line and lets a long value wrap", () => {
    expect(declaration(shellCss, ".cell-time", "white-space")).toBe("nowrap");
    expect(declaration(shellCss, ".cell-wrap", "white-space")).toBe("pre-wrap");
    expect(declaration(shellCss, ".cell-wrap", "word-break")).toBe("break-word");
  });

  it("gives a collection title a floor to collapse to", () => {
    expect(declaration(collectionsCss, ".cell-title", "min-width")).toBe("9rem");
  });
});

describe("row 108: .row-actions has styles", () => {
  it("lays the row's buttons out in a wrapping row with a gap", () => {
    expect(declaration(shellCss, ".row-actions", "display")).toBe("flex");
    expect(declaration(shellCss, ".row-actions", "flex-wrap")).toBe("wrap");
    expect(declaration(shellCss, ".row-actions", "gap")).toBe("8px");
  });
});

describe("button labels", () => {
  it("keeps every label on one line", () => {
    expect(declaration(themeCss, "button", "white-space")).toBe("nowrap");
  });
});

describe("the sidebar stays reachable", () => {
  it("pins the rail to the viewport with its own scrollbar", () => {
    expect(declaration(shellCss, ".sidebar", "position")).toBe("sticky");
    expect(declaration(shellCss, ".sidebar", "top")).toBe("0");
    // Two height declarations on purpose: dvh (the small mobile viewport,
    // so the rail's last item cannot hide under the URL bar) must come
    // LAST so it wins where it parses, with vh as the older-engine
    // fallback above it. `declaration` returns the last one.
    expect(declaration(shellCss, ".sidebar", "height")).toBe("100dvh");
    expect(shellCss).toContain("height: 100vh;");
    expect(declaration(shellCss, ".sidebar", "overflow-y")).toBe("auto");
  });

  it("keeps the mark and wordmark together while the toggle holds the right edge", () => {
    expect(declaration(shellCss, ".sidebar-brand-name", "flex")).toBe("1");
  });
});

describe("the log line restructures below 640px", () => {
  it("drops the fixed logger column and puts the message on its own row", () => {
    const columns = declaration(logsCss, ".log-line", "grid-template-columns", MOBILE);
    expect(columns).not.toContain("14rem");
    expect(declaration(logsCss, ".log-message", "grid-column", MOBILE)).toBe("1 / -1");
  });

  it("leaves the desktop grid alone", () => {
    expect(declaration(logsCss, ".log-line", "grid-template-columns")).toBe(
      "5.2rem 4.6rem 14rem 1fr",
    );
  });

  it("re-reserves the viewport height for controls that now stack", () => {
    expect(declaration(logsCss, ".log-controls", "flex-wrap", MOBILE)).toBe("wrap");
    expect(declaration(logsCss, ".log-viewport", "height", MOBILE)).toBe("calc(100vh - 17rem)");
    expect(declaration(logsCss, ".log-viewport", "min-height", MOBILE)).toBe("12rem");
  });
});

describe("the manual-source controls fit a narrow panel", () => {
  it("wraps the item panel's input and button rather than overhanging", () => {
    expect(declaration(itemCss, ".manual-controls", "flex-wrap")).toBe("wrap");
    expect(declaration(itemCss, ".manual-input", "max-width")).toBe("100%");
  });

  it("wraps the collection row's poster form the same way", () => {
    expect(declaration(collectionsCss, ".poster-form", "flex-wrap")).toBe("wrap");
    expect(declaration(collectionsCss, ".poster-input", "max-width")).toBe("100%");
  });
});

describe("the testing grid stays inside its box", () => {
  it("clips a sample to its preview box rather than widening the cell", () => {
    expect(declaration(testingCss, ".sample-view", "overflow")).toBe("hidden");
    expect(declaration(testingCss, ".sample-image", "object-fit")).toBe("contain");
  });
});

describe("the mode cards fit a phone", () => {
  it("drops to one column below 640px, under the 420px track width", () => {
    // The track is wider than the viewport there, so the auto-fit grid would
    // otherwise hand every card its own horizontal scrollbar.
    expect(declaration(modesCss, ".mode-grid", "grid-template-columns")).toBe(
      "repeat(auto-fit, minmax(420px, 1fr))",
    );
    expect(declaration(modesCss, ".mode-grid", "grid-template-columns", MOBILE)).toBe("1fr");
  });

  it("stacks a filter label over its control below 640px", () => {
    expect(declaration(modesCss, ".mode-filters label", "flex-direction", MOBILE)).toBe(
      "column",
    );
    expect(declaration(modesCss, ".mode-filters label", "align-items", MOBILE)).toBe(
      "stretch",
    );
  });

  it("stops a number input overhanging the card at any width", () => {
    // The input's own selector, not the select's. The two share a grouped
    // rule, so asserting the select would keep passing after the input was
    // dropped out of the group -- which is exactly the guard this pins.
    expect(declaration(modesCss, ".mode-filters input", "max-width")).toBe("100%");
    expect(declaration(modesCss, '.mode-filters input[type="number"]', "width")).toBe("7rem");
  });

  it("lets the confirmation sentence take its own line when the row wraps", () => {
    expect(declaration(modesCss, ".mode-confirm", "flex")).toBe("1 1 14rem");
  });
});

describe("the id-mismatch rows fit a narrow table", () => {
  it("wraps a row's ids rather than handing the column the width of three", () => {
    // A series carries tvdb, tmdb and imdb on each side; in one line they are
    // wider than a phone viewport, and the table would scroll for every row.
    expect(declaration(mismatchesCss, ".mismatch-ids", "flex-wrap")).toBe("wrap");
    expect(declaration(mismatchesCss, ".mismatch-ids", "min-width")).toBe("8rem");
    // Each chip stays on one line -- "tmdb 438631" broken across two is not an
    // id any more.
    expect(declaration(mismatchesCss, ".mismatch-id", "white-space")).toBe("nowrap");
  });

  it("marks a disagreeing id by more than its colour", () => {
    expect(declaration(mismatchesCss, ".mismatch-id.differs", "border-color")).toBe(
      "var(--error)",
    );
    expect(declaration(mismatchesCss, ".mismatch-id.differs", "font-weight")).toBe("600");
  });

  it("wraps the scan control and its explanation", () => {
    expect(declaration(mismatchesCss, ".mismatch-controls", "flex-wrap")).toBe("wrap");
  });
});

describe("the catalog picker's tab strip fits a phone", () => {
  it("wraps the strip rather than hiding categories off the right edge", () => {
    // Nine labels are wider than a phone in one line. A scrolling strip would
    // put Time and Production behind a gesture nothing announces, so the strip
    // wraps at every width -- this is not a mobile-only rule.
    expect(declaration(catalogCss, ".catalog-tabs", "display")).toBe("flex");
    expect(declaration(catalogCss, ".catalog-tabs", "flex-wrap")).toBe("wrap");
  });

  it("marks the selected tab by more than its colour", () => {
    // The same rule the mismatch table follows for a disagreeing id: colour
    // alone is the distinction a colour-blind operator does not get.
    expect(declaration(catalogCss, '.catalog-tab[aria-selected="true"]', "font-weight")).toBe(
      "600",
    );
    expect(declaration(catalogCss, '.catalog-tab[aria-selected="true"]', "box-shadow")).toBe(
      "inset 0 -2px 0 0 var(--accent)",
    );
  });

  it("gives a tab a touch-sized target", () => {
    expect(declaration(catalogCss, ".catalog-tab", "min-height")).toBe("40px");
  });
});

describe("the catalog rows fit a narrow panel", () => {
  it("stacks a row's switch over its detail below 640px", () => {
    expect(declaration(catalogCss, ".catalog-row", "display")).toBe("flex");
    expect(declaration(catalogCss, ".catalog-row", "flex-direction", MOBILE)).toBe("column");
    expect(declaration(catalogCss, ".catalog-row", "align-items", MOBILE)).toBe("stretch");
  });

  it("keeps a badge on one line at every width", () => {
    // "needs row 155" broken across two lines is not a row number any more.
    expect(declaration(catalogCss, ".catalog-badge", "white-space")).toBe("nowrap");
  });

  it("lets only the provenance path break, and only on a phone", () => {
    expect(declaration(catalogCss, ".catalog-source", "white-space")).toBe("nowrap");
    expect(declaration(catalogCss, ".catalog-source", "white-space", MOBILE)).toBe("normal");
    expect(declaration(catalogCss, ".catalog-source", "word-break", MOBILE)).toBe("break-word");
  });

  it("wraps the summary strip and its chips", () => {
    expect(declaration(catalogCss, ".catalog-summary", "flex-wrap")).toBe("wrap");
    expect(declaration(catalogCss, ".catalog-count", "white-space")).toBe("nowrap");
  });
});

describe("settings fits the panel", () => {
  it("stacks a config row label over its value below 640px", () => {
    expect(declaration(settingsCss, ".config-row", "flex-direction", MOBILE)).toBe("column");
    expect(declaration(settingsCss, ".config-row", "align-items", MOBILE)).toBe("stretch");
  });

  it("stops a ch-sized input overhanging the panel at any width", () => {
    expect(
      declaration(settingsCss, '.config-value input[type="text"]', "max-width"),
    ).toBe("100%");
  });
});
