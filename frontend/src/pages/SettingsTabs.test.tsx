/** The tab bar's two invariants.
 *
 * Every section the server serves has exactly one tab (spec section 10's
 * "the Settings page's tabs hold every section the served config has, each
 * exactly once"), and the accordion remembers which section was open. */
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsAccordion } from "./SettingsAccordion";
import { SECTION_TAB, TABS, tabForSection, useOpenSection } from "./settingsTabs";

/** A working `localStorage`, private to one test -- `unstubGlobals` in
 * vite.config.ts removes the stub again afterwards. Vitest 5's jsdom
 * `localStorage` lives as long as the file's window, so without a fresh one
 * per test a write in one test would be the value the next test's mount
 * reads back. */
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

/** A `localStorage` that throws on every call, the way a private window or
 * blocked site data does. */
function stubThrowingStorage(): void {
  const blocked = () => {
    throw new Error("storage blocked");
  };
  const store: Storage = {
    get length() {
      return 0;
    },
    key: blocked,
    getItem: blocked,
    setItem: blocked,
    removeItem: blocked,
    clear: blocked,
  };
  vi.stubGlobal("localStorage", store);
}

/** Every top-level object key `GET /api/config` serves today. Written out
 * rather than derived, because the point of the test is to notice when the
 * server grows one and nobody assigned it. */
const SERVED_SECTIONS = [
  "plex", "jellyfin", "providers", "artwork", "operations", "badges",
  "collections", "playlists", "cleanup", "prune", "merge", "maintenance",
  "libraries", "artwork_modes", "scheduler", "adopt", "radarr", "sonarr",
  "tracearr", "arr_sync", "notifications",
];

describe("the tab map", () => {
  it("gives every served section exactly one tab", () => {
    for (const section of SERVED_SECTIONS) {
      const tab = tabForSection(section);
      expect(TABS.map((entry) => entry.id)).toContain(tab);
      expect(
        SERVED_SECTIONS.filter((other) => other === section),
      ).toHaveLength(1);
    }
  });

  it("assigns a section it has never seen to System rather than nowhere", () => {
    expect(tabForSection("a_section_from_the_future")).toBe("system");
  });

  it("names the seven tabs in the designed order", () => {
    expect(TABS.map((entry) => entry.label)).toEqual([
      "Servers", "Libraries", "Artwork", "Collections", "Metadata",
      "Integrations", "System",
    ]);
  });

  it("has no section assigned to a tab that is not in TABS", () => {
    const ids = new Set(TABS.map((entry) => entry.id));
    for (const tab of Object.values(SECTION_TAB)) expect(ids.has(tab)).toBe(true);
  });
});

describe("SettingsAccordion", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders its body only when open", () => {
    const { rerender } = render(
      <SettingsAccordion title="Scheduler" open={false} onToggle={() => {}}>
        <p>the body</p>
      </SettingsAccordion>,
    );
    expect(screen.queryByText("the body")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Scheduler" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    rerender(
      <SettingsAccordion title="Scheduler" open onToggle={() => {}}>
        <p>the body</p>
      </SettingsAccordion>,
    );
    expect(screen.getByText("the body")).toBeInTheDocument();
  });

  it("carries the restart pill with its reason as hover text", () => {
    render(
      <SettingsAccordion
        title="Workers"
        open
        onToggle={() => {}}
        restartReason="the worker pool is sized once when the process starts"
      >
        <p>body</p>
      </SettingsAccordion>,
    );
    const pill = screen.getByText("restart to apply");
    expect(pill).toHaveAttribute(
      "title",
      "the worker pool is sized once when the process starts",
    );
  });

  it("calls onToggle when the header is clicked", () => {
    const seen: boolean[] = [];
    render(
      <SettingsAccordion title="Scheduler" open={false} onToggle={() => seen.push(true)}>
        <p>body</p>
      </SettingsAccordion>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Scheduler" }));
    expect(seen).toEqual([true]);
  });
});

describe("useOpenSection", () => {
  beforeEach(() => {
    stubStorage();
  });

  it("remembers the open section across a remount", () => {
    const first = renderHook(() => useOpenSection("system"));
    expect(first.result.current[0]).toBeNull();
    act(() => first.result.current[1]("scheduler"));
    expect(first.result.current[0]).toBe("scheduler");
    first.unmount();

    const second = renderHook(() => useOpenSection("system"));
    expect(second.result.current[0]).toBe("scheduler");
  });

  it("keeps one open section per tab, not one for the whole page", () => {
    const system = renderHook(() => useOpenSection("system"));
    act(() => system.result.current[1]("scheduler"));

    const artwork = renderHook(() => useOpenSection("artwork"));
    expect(artwork.result.current[0]).toBeNull();
  });

  it("falls back to nothing open when a read throws", () => {
    stubThrowingStorage();
    const { result } = renderHook(() => useOpenSection("system"));
    expect(result.current[0]).toBeNull();
  });

  it("does not throw when a write is blocked, and still updates in memory", () => {
    stubThrowingStorage();
    const { result } = renderHook(() => useOpenSection("system"));
    expect(() => act(() => result.current[1]("scheduler"))).not.toThrow();
    expect(result.current[0]).toBe("scheduler");
  });
});
