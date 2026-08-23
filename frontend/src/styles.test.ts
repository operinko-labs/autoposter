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

const collectionsCss = stylesheet("./pages/collections.css");
const logsCss = stylesheet("./pages/logs.css");
const settingsCss = stylesheet("./pages/settings.css");
const shellCss = stylesheet("./shell/shell.css");
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
    expect(declaration(shellCss, ".sidebar", "height")).toBe("100vh");
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
