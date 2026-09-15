/** Which tab a configuration section lives on, and which section is open.
 *
 * A table rather than a heuristic, because the grouping is editorial: spec
 * section 7 decided that `providers` belongs beside `artwork` (it is where art
 * comes from) and `notifications` beside the *arrs (they are all other
 * services). Nothing about the schema says either.
 *
 * The fallback matters as much as the table. A section added to the config
 * that nobody assigned here still gets a home -- System -- rather than
 * vanishing from a page that renders only what it recognises. That failure
 * would be silent, and the setting would simply be unreachable. */
import { useCallback, useState } from "react";

export type TabId =
  | "servers"
  | "libraries"
  | "artwork"
  | "collections"
  | "metadata"
  | "integrations"
  | "system";

export const TABS: { id: TabId; label: string }[] = [
  { id: "servers", label: "Servers" },
  { id: "libraries", label: "Libraries" },
  { id: "artwork", label: "Artwork" },
  { id: "collections", label: "Collections" },
  { id: "metadata", label: "Metadata" },
  { id: "integrations", label: "Integrations" },
  { id: "system", label: "System" },
];

export const SECTION_TAB: Record<string, TabId> = {
  plex: "servers",
  jellyfin: "servers",

  libraries: "libraries",
  cleanup: "libraries",
  prune: "libraries",
  merge: "libraries",
  adopt: "libraries",

  artwork: "artwork",
  badges: "artwork",
  providers: "artwork",
  artwork_modes: "artwork",

  collections: "collections",
  playlists: "collections",

  operations: "metadata",
  maintenance: "metadata",

  radarr: "integrations",
  sonarr: "integrations",
  tracearr: "integrations",
  arr_sync: "integrations",
  notifications: "integrations",

  scheduler: "system",
};

/** Where the top-level scalars (`workers`, `assets_root`, `api_docs_enabled`,
 * ...) are gathered, as one "General" accordion. */
export const GENERAL_TAB: TabId = "system";

export function tabForSection(section: string): TabId {
  return SECTION_TAB[section] ?? "system";
}

const STORAGE_KEY = "autoposter.settings.open";

function readOpen(): Record<string, string> {
  // Every read and write is guarded: a private window, blocked site data or a
  // quota error must not take the Settings page down over a convenience.
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw === null ? {} : JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

/** The section open on `tab`, remembered per browser (spec section 7:
 * "collapsed by default except the one last opened"). One section per tab, so
 * moving between tabs does not unfold the whole page. */
export function useOpenSection(
  tab: TabId,
): [string | null, (section: string | null) => void] {
  const [all, setAll] = useState<Record<string, string>>(readOpen);
  const set = useCallback(
    (section: string | null) => {
      setAll((current) => {
        const next = { ...current };
        if (section === null) delete next[tab];
        else next[tab] = section;
        try {
          window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
        } catch {
          // Remembering is a convenience; the page works without it.
        }
        return next;
      });
    },
    [tab],
  );
  return [all[tab] ?? null, set];
}
