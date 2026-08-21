import { NavLink } from "react-router-dom";

import { useSession } from "../auth/SessionContext";
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
  settings:
    "M19.14 12.94a7.07 7.07 0 0 0 0-1.88l2.03-1.58a.5.5 0 0 0 .12-.62l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54c-.58.24-1.12.55-1.62.94l-2.39-.96a.5.5 0 0 0-.6.22L2.67 8.86a.5.5 0 0 0 .12.62l2.03 1.58a7.07 7.07 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.62l1.92 3.32c.13.22.39.3.6.22l2.39-.96c.5.39 1.04.7 1.62.94l.36 2.54c.04.24.25.42.5.42h3.84c.25 0 .46-.18.5-.42l.36-2.54c.58-.24 1.12-.55 1.62-.94l2.39.96c.22.08.47 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.62l-2.03-1.58zM12 15.6A3.6 3.6 0 1 1 12 8.4a3.6 3.6 0 0 1 0 7.2z",
};

const NAV = [
  { to: "/", label: "Dashboard", icon: "dashboard", end: true },
  { to: "/library", label: "Library", icon: "library", end: false },
  { to: "/collections", label: "Collections", icon: "collections", end: false },
  { to: "/failures", label: "Failures", icon: "failures", end: false },
  { to: "/settings", label: "Settings", icon: "settings", end: false },
];

export function Sidebar() {
  const { logout } = useSession();

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">Autoposter</div>

      <ul className="sidebar-nav">
        {NAV.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                isActive ? "sidebar-link active" : "sidebar-link"
              }
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d={ICONS[item.icon]} />
              </svg>
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>

      <button className="sidebar-logout" type="button" onClick={() => void logout()}>
        Sign out
      </button>
    </nav>
  );
}
