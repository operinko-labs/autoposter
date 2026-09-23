import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { SessionProvider, useSession } from "./auth/SessionContext";
import { useSetupProbe } from "./auth/useSetupProbe";
import { PageErrorBoundary } from "./shell/PageErrorBoundary";
import { Sidebar } from "./shell/Sidebar";
import "./shell/shell.css";

/* Every page is its own chunk (perf spec A3). The entry carries the shell,
 * the router and the session plumbing; a page's code -- and recharts behind
 * the Dashboard's charts above all -- downloads the first time that page is
 * opened. Each `.then` adapts a page's named export to the default export
 * React.lazy expects, so no page module changes shape. */
const ActionCenter = lazy(() =>
  import("./pages/ActionCenter").then((module) => ({ default: module.ActionCenter })),
);
const Collections = lazy(() =>
  import("./pages/Collections").then((module) => ({ default: module.Collections })),
);
const Dashboard = lazy(() =>
  import("./pages/Dashboard").then((module) => ({ default: module.Dashboard })),
);
const Failures = lazy(() =>
  import("./pages/Failures").then((module) => ({ default: module.Failures })),
);
const Files = lazy(() => import("./pages/Files").then((module) => ({ default: module.Files })));
const ItemDetail = lazy(() =>
  import("./pages/ItemDetail").then((module) => ({ default: module.ItemDetail })),
);
const Jobs = lazy(() => import("./pages/Jobs").then((module) => ({ default: module.Jobs })));
const Library = lazy(() =>
  import("./pages/Library").then((module) => ({ default: module.Library })),
);
const Login = lazy(() => import("./pages/Login").then((module) => ({ default: module.Login })));
const Logs = lazy(() => import("./pages/Logs").then((module) => ({ default: module.Logs })));
const Mismatches = lazy(() =>
  import("./pages/Mismatches").then((module) => ({ default: module.Mismatches })),
);
const Modes = lazy(() => import("./pages/Modes").then((module) => ({ default: module.Modes })));
const Settings = lazy(() =>
  import("./pages/Settings").then((module) => ({ default: module.Settings })),
);
const Setup = lazy(() => import("./pages/Setup").then((module) => ({ default: module.Setup })));
const Testing = lazy(() =>
  import("./pages/Testing").then((module) => ({ default: module.Testing })),
);

function AuthenticatedApp() {
  const { pathname } = useLocation();
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">
        {/* One boundary for every page, inside the shell: the sidebar stays
            put while a page's chunk arrives, and only the page area waits --
            or, if the chunk never arrives, only the page area says so. */}
        <PageErrorBoundary resetKey={pathname}>
          <Suspense fallback={<p className="muted">Loading…</p>}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/actions" element={<ActionCenter />} />
              <Route path="/library" element={<Library />} />
              <Route path="/items/:itemId" element={<ItemDetail />} />
              <Route path="/collections" element={<Collections />} />
              <Route path="/jobs" element={<Jobs />} />
              <Route path="/failures" element={<Failures />} />
              <Route path="/files" element={<Files />} />
              <Route path="/mismatches" element={<Mismatches />} />
              <Route path="/modes" element={<Modes />} />
              <Route path="/logs" element={<Logs />} />
              <Route path="/testing" element={<Testing />} />
              <Route path="/settings" element={<Settings />} />
              {/* The server serves index.html for any unclaimed path, so an
                  unknown URL reaches the router rather than a 404 page. */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </PageErrorBoundary>
      </main>
    </div>
  );
}

function Gate() {
  const { authenticated } = useSession();
  const setupRequired = useSetupProbe();
  const { pathname } = useLocation();

  if (authenticated) return <AuthenticatedApp />;
  // Nothing is rendered until the probe answers. Showing the login form first
  // and replacing it a moment later is exactly the flicker an operator on a
  // fresh deployment would read as "the wizard is broken" -- and this is the
  // one screen where they have no prior expectation to correct it against.
  if (setupRequired === null) return null;
  // Setup and Login are lazy chunks too, outside the shell, so they get their
  // own boundary -- drawing nothing while one loads, the same no-flicker rule
  // as the probe wait just above -- and their own error boundary.
  return (
    <PageErrorBoundary resetKey={pathname}>
      <Suspense fallback={null}>
        {setupRequired ? (
          <Routes>
            <Route path="/setup" element={<Setup />} />
            {/* An unconfigured deployment has exactly one page. Any other URL --
                a bookmark from a working install, a reload of /settings -- lands
                on it rather than on a router miss. */}
            <Route path="*" element={<Navigate to="/setup" replace />} />
          </Routes>
        ) : (
          <Login />
        )}
      </Suspense>
    </PageErrorBoundary>
  );
}

export function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Gate />
      </BrowserRouter>
    </SessionProvider>
  );
}
