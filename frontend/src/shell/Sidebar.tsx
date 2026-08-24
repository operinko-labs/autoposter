import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";

import { apiFetch } from "../api/client";
import type { VersionResponse } from "../api/types";
import { useSession } from "../auth/SessionContext";
import logoMark from "../assets/logo-small.svg";
import "./shell.css";

/** Inline rather than an icon package: four icons do not justify a dependency
 * that ships several thousand, and the build must not fetch an icon font at
 * runtime in a cluster-internal deployment. */
const ICONS: Record<string, string> = {
  dashboard: "M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z",
  library:
    "M4 2h16a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm2 2v2h2V4H6zm10 0v2h2V4h-2zM6 8v8h12V8H6zm0 10v2h2v-2H6zm10 0v2h2v-2h-2z",
  collections:
    "M4 6H2v14a2 2 0 0 0 2 2h14v-2H4V6zm16-4H8a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2z",
  failures:
    "M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z",
  jobs:
    "M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm.5-13H11v6l5.25 3.15.75-1.23-4.5-2.67V7z",
  logs: "M3 4h18v2H3V4zm0 4h12v2H3V8zm0 4h18v2H3v-2zm0 4h12v2H3v-2zm0 4h18v2H3v-2z",
  mismatches: "M9.01 14H2v2h7.01v3L13 15l-3.99-4v3zm5.98-1v-3H22V8h-7.01V5L11 9l3.99 4z",
  modes: "M8 5v14l11-7L8 5zM4 5h2v14H4V5z",
  settings:
    "M19.14 12.94a7.07 7.07 0 0 0 0-1.88l2.03-1.58a.5.5 0 0 0 .12-.62l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54c-.58.24-1.12.55-1.62.94l-2.39-.96a.5.5 0 0 0-.6.22L2.67 8.86a.5.5 0 0 0 .12.62l2.03 1.58a7.07 7.07 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.62l1.92 3.32c.13.22.39.3.6.22l2.39-.96c.5.39 1.04.7 1.62.94l.36 2.54c.04.24.25.42.5.42h3.84c.25 0 .46-.18.5-.42l.36-2.54c.58-.24 1.12-.55 1.62-.94l2.39.96c.22.08.47 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.62l-2.03-1.58zM12 15.6A3.6 3.6 0 1 1 12 8.4a3.6 3.6 0 0 1 0 7.2z",
  testing:
    "M13 11.33 18 18H6l5-6.67V6h2m3.96-2H8.04c-.42 0-.65.48-.39.81L9 6.5V11L3.4 18.6c-.49.66-.02 1.6.8 1.6h15.6c.82 0 1.29-.94.8-1.6L15 11V6.5l1.35-1.69c.26-.33.03-.81-.39-.81z",
  logout:
    "M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.59L17 17l5-5-5-5zM4 5h8V3H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8v-2H4V5z",
  collapse: "M15.4 7.4 14 6l-6 6 6 6 1.4-1.4L10.8 12l4.6-4.6z",
  expand: "M8.6 7.4 10 6l6 6-6 6-1.4-1.4 4.6-4.6-4.6-4.6z",
};

const NAV = [
  { to: "/", label: "Dashboard", icon: "dashboard", end: true },
  { to: "/library", label: "Library", icon: "library", end: false },
  { to: "/collections", label: "Collections", icon: "collections", end: false },
  // Before Failures: the live queue is the question an operator has while a
  // pass is running, and Failures is what is left over once it has stopped.
  { to: "/jobs", label: "Jobs", icon: "jobs", end: false },
  { to: "/failures", label: "Failures", icon: "failures", end: false },
  // After Failures: this is what a stubborn failure usually turns out to be --
  // Plex and Radarr/Sonarr matched the same folder to different titles -- and
  // it is the page an operator reaches for once Failures has stopped
  // explaining itself.
  { to: "/mismatches", label: "ID mismatches", icon: "mismatches", end: false },
  { to: "/modes", label: "Run modes", icon: "modes", end: false },
  { to: "/logs", label: "Logs", icon: "logs", end: false },
  { to: "/testing", label: "Testing", icon: "testing", end: false },
  { to: "/settings", label: "Settings", icon: "settings", end: false },
];

const STORAGE_KEY = "autoposter.sidebar";

/** Below this the sidebar alone eats most of a phone screen, so it starts as
 * the icon rail. Matches the 768px tablet breakpoint the pages use. */
const NARROW = "(max-width: 767px)";

/** localStorage rather than the sessionStorage `api/client.ts` uses for the
 * token: a layout preference is not a credential, and it should survive the
 * tab closing. The guarding is the same -- storage access throws outright
 * under some privacy settings, and a blocked read must degrade to "no
 * preference stored", never to a blank sidebar. */
function readStored(): boolean | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw === "collapsed") return true;
    if (raw === "expanded") return false;
    return null;
  } catch {
    return null;
  }
}

function writeStored(collapsed: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, collapsed ? "collapsed" : "expanded");
  } catch {
    // Blocked; the choice still holds for the life of the page.
  }
}

function isNarrow(): boolean {
  return window.matchMedia?.(NARROW).matches ?? false;
}

export function Sidebar() {
  const { logout } = useSession();

  // Read once, at mount. A stored choice is the user's; without one the
  // viewport decides.
  const [explicit, setExplicit] = useState(() => readStored() !== null);
  const [collapsed, setCollapsed] = useState(() => readStored() ?? isNarrow());

  useEffect(() => {
    // Only while the user has not chosen: rotating a tablet should move the
    // sidebar with the viewport, but an explicit toggle outranks the
    // viewport and must not be undone by a resize.
    if (explicit) return;
    const query = window.matchMedia?.(NARROW);
    if (!query) return;
    const onChange = (event: MediaQueryListEvent) => setCollapsed(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [explicit]);

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    setExplicit(true);
    writeStored(next);
  };

  // Fetched once, at mount, and never polled. The server caches Harbor's
  // answer for fifteen minutes, so a refetch would mostly re-read the same
  // string; and the sidebar mounts on every full page load, which is often
  // enough for a line that changes when the pod is replaced. A failure leaves
  // this null and the line simply does not render -- the shell must not show
  // an error for a decoration.
  const [version, setVersion] = useState<VersionResponse | null>(null);
  useEffect(() => {
    let cancelled = false;
    void apiFetch<VersionResponse>("/api/version")
      .then((response) => {
        if (!cancelled) setVersion(response);
      })
      .catch(() => {
        // Including the 401 on a dead session: `apiFetch` has already routed
        // to the login form by then, and this is the one caller with nothing
        // to say about it.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggleLabel = collapsed ? "Expand sidebar" : "Collapse sidebar";
  // Only a true is a marker. A null means the registry was not asked or could
  // not be reached, which is not evidence of being up to date.
  const updateAvailable = version?.update_available === true;

  return (
    <nav className={collapsed ? "sidebar collapsed" : "sidebar"}>
      <div className="sidebar-brand">
        {/* Mark first, then the wordmark: that is the order in
            docs/logo-lockup.svg, and it is what makes the collapsed rail --
            which keeps only the mark -- read as the same lockup with its
            right-hand half removed. */}
        <img className="sidebar-brand-mark" src={logoMark} alt="Autoposter" width={24} height={24} />
        <span className="sidebar-brand-name">Autoposter</span>
        <button
          className="sidebar-toggle"
          type="button"
          onClick={toggle}
          aria-expanded={!collapsed}
          aria-controls="sidebar-nav"
          title={toggleLabel}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d={collapsed ? ICONS.expand : ICONS.collapse} />
          </svg>
          <span className="sidebar-hidden-label">{toggleLabel}</span>
        </button>
      </div>

      <ul className="sidebar-nav" id="sidebar-nav">
        {NAV.map((item) => (
          <li key={item.to}>
            {/* The label stays in the DOM when collapsed -- CSS clips it out
                of sight rather than removing it, so the link keeps its
                accessible name for a screen reader. `title` gives the same
                name to a sighted user as a tooltip. */}
            <NavLink
              to={item.to}
              end={item.end}
              title={item.label}
              className={({ isActive }) =>
                isActive ? "sidebar-link active" : "sidebar-link"
              }
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d={ICONS[item.icon]} />
              </svg>
              <span className="sidebar-label">{item.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>

      {/* Above Sign out, and only once the answer is in: a version line that
          renders empty and then fills would shift the two controls under the
          operator's cursor. Both the tag and the marker's text live in
          `.sidebar-label`, so the collapsed rail clips them out of sight while
          keeping them in the accessibility tree -- the same treatment every
          link above gets. What survives the collapse is the marker's dot,
          named by that clipped label and by `title`. */}
      {version !== null && (
        <div className="sidebar-version">
          <span className="sidebar-label sidebar-version-tag">{version.version}</span>
          {updateAvailable && (
            <span className="sidebar-update" title="Update available">
              <span className="sidebar-update-dot" aria-hidden="true" />
              <span className="sidebar-label">Update available</span>
            </span>
          )}
        </div>
      )}

      <button
        className="sidebar-logout"
        type="button"
        onClick={() => void logout()}
        title="Sign out"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d={ICONS.logout} />
        </svg>
        <span className="sidebar-label">Sign out</span>
      </button>
    </nav>
  );
}
