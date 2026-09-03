import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

/** The test brings its own `window.localStorage`, and every test gets a fresh
 * one from `beforeEach`. Vitest 4's jsdom exposed no storage at all (a write
 * from a toggle was a no-op); vitest 5's exposes jsdom's own, which lives as
 * long as the file's window -- so an "Expand sidebar" click in one test was
 * the stored choice the next test's mount restored. `unstubGlobals` removes
 * the stub again after each test. */
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

const RUNNING = "sha-4b2a34d";

/** `GET /api/version` issued but not yet answered.
 *
 * The default for every test in this file, because the sidebar now fetches on
 * mount and the tests above it are synchronous. Left unstubbed they would
 * reach jsdom's own `fetch`; stubbed with a *resolving* promise they would
 * each update state after their last assertion, which React reports as an
 * unwrapped `act()`. A request still in flight is what a synchronous render
 * sees in a browser too, so this is the honest default rather than a
 * workaround -- and it is what proves the layout tests do not depend on the
 * version line existing. */
function stubPendingVersion() {
  const fetchMock = vi.fn().mockReturnValue(new Promise<Response>(() => {}));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** `GET /api/version` answered with `body`. */
function stubVersion(
  body: unknown = { version: RUNNING, latest: null, update_available: null },
  status = 200,
) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  stubStorage();
  stubPendingVersion();
});

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

  it("leads with the mark, then the wordmark, then the toggle", () => {
    stubMatchMedia(false);

    renderSidebar();

    // DOM order, not CSS order: the lockup in docs/logo-lockup.svg puts the
    // mark first, and the collapsed rail keeps the mark alone -- so the
    // expanded header has to read as that same lockup, not as its mirror.
    // `compareDocumentPosition` rather than an index into children, because
    // the assertion is about the order of these three and not about how many
    // nodes the header happens to contain.
    const mark = screen.getByRole("img", { name: "Autoposter" });
    const wordmark = screen.getByText("Autoposter", { selector: ".sidebar-brand-name" });
    const toggle = screen.getByRole("button", { name: "Collapse sidebar" });

    expect(mark.compareDocumentPosition(wordmark)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(wordmark.compareDocumentPosition(toggle)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
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

  it("offers a Testing entry that routes to /testing", () => {
    stubMatchMedia(false);

    renderSidebar();

    const link = screen.getByRole("link", { name: "Testing" });
    expect(link).toHaveAttribute("href", "/testing");
    expect(link).toHaveAttribute("title", "Testing");
  });

  it("reaches the Action Center", () => {
    stubMatchMedia(false);

    renderSidebar();

    // The queue is only reachable from here; a route with no link into it is
    // a page that ships and is never found.
    const link = screen.getByRole("link", { name: "Action Center" });
    expect(link).toHaveAttribute("href", "/actions");
    expect(link).toHaveAttribute("title", "Action Center");

    // Directly after Dashboard, per the NAV comment: "what needs me" is the
    // question an operator asks immediately after "what is happening".
    const dashboard = screen.getByRole("link", { name: "Dashboard" });
    expect(dashboard.compareDocumentPosition(link)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("reaches the id-mismatch view", () => {
    stubMatchMedia(false);

    renderSidebar();

    // The scan is only reachable from here; a route with no link into it is a
    // page that ships and is never found.
    expect(screen.getByRole("link", { name: "ID mismatches" })).toHaveAttribute(
      "href",
      "/mismatches",
    );
  });

  it("reaches the run-modes page", () => {
    stubMatchMedia(false);

    renderSidebar();

    // The six artwork modes are only reachable from here; a route with no
    // link into it is a page that ships and is never found.
    expect(screen.getByRole("link", { name: "Run modes" })).toHaveAttribute(
      "href",
      "/modes",
    );
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

describe("Sidebar version line", () => {
  it("shows the running version just above Sign out", async () => {
    stubMatchMedia(false);
    stubVersion({ version: RUNNING, latest: null, update_available: null });

    renderSidebar();

    const tag = await screen.findByText(RUNNING);
    // Placement is the requirement, not merely presence: this is a footer
    // note, and above the navigation it would read as a heading.
    const signOut = screen.getByRole("button", { name: "Sign out" });
    expect(tag.compareDocumentPosition(signOut)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("marks an update when the registry holds a newer image", async () => {
    stubMatchMedia(false);
    stubVersion({
      version: RUNNING,
      latest: "sha-9f10c2e",
      update_available: true,
    });

    renderSidebar();

    expect(await screen.findByText("Update available")).toBeInTheDocument();
    // The running version stays visible beside the marker: "there is an
    // update" is only actionable next to "from what".
    expect(screen.getByText(RUNNING)).toBeInTheDocument();
  });

  it("shows no marker when the running version is the newest", async () => {
    stubMatchMedia(false);
    stubVersion({ version: RUNNING, latest: RUNNING, update_available: false });

    renderSidebar();

    await screen.findByText(RUNNING);
    expect(screen.queryByText("Update available")).not.toBeInTheDocument();
  });

  it("shows no marker when the registry was not asked", async () => {
    stubMatchMedia(false);
    // What an unconfigured deployment answers: a null is "unknown", and
    // rendering it as "up to date" would be a claim the server did not make.
    stubVersion({ version: RUNNING, latest: null, update_available: null });

    renderSidebar();

    await screen.findByText(RUNNING);
    expect(screen.queryByText("Update available")).not.toBeInTheDocument();
  });

  it("renders nothing at all when the version cannot be read", async () => {
    stubMatchMedia(false);
    stubVersion({ detail: "boom" }, 500);

    renderSidebar();

    // The shell must not show an error for a decoration -- Sign out and the
    // navigation are unaffected, and there is simply no version line.
    await screen.findByRole("button", { name: "Sign out" });
    await waitFor(() => {
      expect(document.querySelector(".sidebar-version")).toBeNull();
    });
  });

  it("keeps the collapsed rail's marker named while its text is out of sight", async () => {
    stubMatchMedia(true);
    stubVersion({
      version: RUNNING,
      latest: "sha-9f10c2e",
      update_available: true,
    });

    renderSidebar();

    const marker = await screen.findByTitle("Update available");
    expect(sidebarElement()).toHaveClass("collapsed");
    // Same contract as the links above: the text is clipped by CSS, not
    // removed, so the dot that is all a sighted user sees still carries a
    // name -- and `title` gives them the same word on hover.
    expect(marker.querySelector(".sidebar-label")).toHaveTextContent("Update available");
    expect(marker.querySelector(".sidebar-update-dot")).not.toBeNull();
    // The version tag is clipped by the very same rule rather than dropped.
    expect(screen.getByText(RUNNING)).toHaveClass("sidebar-label");
  });

  it("asks for the version once per mount and never polls", async () => {
    stubMatchMedia(false);
    const fetchMock = stubVersion();

    renderSidebar();
    await screen.findByText(RUNNING);

    // The server caches Harbor's answer for fifteen minutes, so a poll would
    // mostly re-read one string; the mount is frequent enough on its own.
    const calls = fetchMock.mock.calls.filter(([path]) => path === "/api/version");
    expect(calls).toHaveLength(1);

    // And nothing schedules a second one afterwards.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(
      fetchMock.mock.calls.filter(([path]) => path === "/api/version"),
    ).toHaveLength(1);
  });
});
