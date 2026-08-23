import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { SessionProvider } from "../auth/SessionContext";
import { Sidebar } from "./Sidebar";

/** jsdom's own `matchMedia` reports `matches: false` for everything and never
 * fires `change`, so both halves of the responsive default -- the narrow
 * viewport, and the viewport crossing the breakpoint while mounted -- are
 * untestable without this. `unstubGlobals` in vite.config.ts puts jsdom's back
 * for the next test. */
function stubMatchMedia(narrow: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  const query = {
    matches: narrow,
    media: "(max-width: 767px)",
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
      listeners.add(listener);
    },
    removeEventListener: (
      _type: string,
      listener: (event: MediaQueryListEvent) => void,
    ) => {
      listeners.delete(listener);
    },
  };
  vi.stubGlobal("matchMedia", vi.fn(() => query));
  return {
    /** Drive the media query across the breakpoint, as a resize would. */
    cross(matches: boolean) {
      query.matches = matches;
      act(() => {
        for (const listener of listeners) {
          listener({ matches } as MediaQueryListEvent);
        }
      });
    },
  };
}

/** This jsdom build exposes no `window.localStorage` at all -- which the
 * component tolerates, and which would make every persistence assertion
 * vacuous -- so the test brings its own. `unstubGlobals` removes it again. */
function stubStorage(): Storage {
  const data = new Map<string, string>();
  const store: Storage = {
    get length() {
      return data.size;
    },
    key: (index: number) => [...data.keys()][index] ?? null,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, String(value)),
    removeItem: (key: string) => void data.delete(key),
    clear: () => data.clear(),
  };
  vi.stubGlobal("localStorage", store);
  return store;
}

function renderSidebar(path = "/") {
  return render(
    <SessionProvider>
      <MemoryRouter initialEntries={[path]}>
        <Sidebar />
      </MemoryRouter>
    </SessionProvider>,
  );
}

function sidebarElement(): HTMLElement {
  const nav = document.querySelector("nav.sidebar");
  if (nav === null) throw new Error("the sidebar did not render");
  return nav as HTMLElement;
}

describe("Sidebar", () => {
  it("starts expanded on a wide viewport", () => {
    stubMatchMedia(false);

    renderSidebar();

    expect(sidebarElement()).not.toHaveClass("collapsed");
    expect(screen.getByRole("button", { name: "Collapse sidebar" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(screen.getByText("Autoposter")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Autoposter" })).toBeInTheDocument();
  });

  it("starts collapsed below 768px", () => {
    stubMatchMedia(true);

    renderSidebar();

    expect(sidebarElement()).toHaveClass("collapsed");
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    // The wordmark is clipped out of sight when collapsed, so the mark alone
    // must still carry the brand's accessible name.
    expect(screen.getByRole("img", { name: "Autoposter" })).toBeInTheDocument();
  });

  it("keeps every collapsed link named and titled while the label is out of sight", () => {
    stubMatchMedia(true);

    renderSidebar();

    // The accessible name survives the collapse -- the label is clipped by
    // CSS, not removed -- and `title` gives a sighted user the same word.
    const link = screen.getByRole("link", { name: "Library" });
    expect(link).toHaveAttribute("title", "Library");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute(
      "title",
      "Settings",
    );
    expect(screen.getByRole("button", { name: "Sign out" })).toHaveAttribute(
      "title",
      "Sign out",
    );
    expect(link.querySelector(".sidebar-label")).toHaveTextContent("Library");
  });

  it("marks the current route active in both states", () => {
    stubMatchMedia(false);

    const expanded = renderSidebar("/library");
    expect(screen.getByRole("link", { name: "Library" })).toHaveClass("active");
    expanded.unmount();

    stubMatchMedia(true);
    renderSidebar("/library");
    expect(sidebarElement()).toHaveClass("collapsed");
    expect(screen.getByRole("link", { name: "Library" })).toHaveClass("active");
  });

  it("toggles, persists the choice, and restores it on the next mount", () => {
    const store = stubStorage();
    stubMatchMedia(false);

    const first = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: "Collapse sidebar" }));

    expect(sidebarElement()).toHaveClass("collapsed");
    expect(store.getItem("autoposter.sidebar")).toBe("collapsed");
    first.unmount();

    // A wide viewport would default to expanded; the stored choice wins.
    stubMatchMedia(false);
    const second = renderSidebar();
    expect(sidebarElement()).toHaveClass("collapsed");

    fireEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
    expect(store.getItem("autoposter.sidebar")).toBe("expanded");
    second.unmount();

    stubMatchMedia(true);
    renderSidebar();
    expect(sidebarElement()).not.toHaveClass("collapsed");
  });

  it("follows the viewport until the user chooses, and stops afterwards", () => {
    const media = stubMatchMedia(false);

    renderSidebar();
    expect(sidebarElement()).not.toHaveClass("collapsed");

    media.cross(true);
    expect(sidebarElement()).toHaveClass("collapsed");

    // An explicit toggle outranks the viewport from then on.
    fireEvent.click(screen.getByRole("button", { name: "Expand sidebar" }));
    media.cross(true);
    expect(sidebarElement()).not.toHaveClass("collapsed");
  });
});
