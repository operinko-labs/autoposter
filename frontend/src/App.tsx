import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { SessionProvider, useSession } from "./auth/SessionContext";
import { Collections } from "./pages/Collections";
import { Dashboard } from "./pages/Dashboard";
import { Failures } from "./pages/Failures";
import { Library } from "./pages/Library";
import { Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import { Sidebar } from "./shell/Sidebar";
import "./shell/shell.css";

function AuthenticatedApp() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/library" element={<Library />} />
          {/* A placeholder until the item detail page lands. The route has to
              exist even so: without it the catch-all below sends every tile's
              link back to the dashboard, which looks like a broken grid rather
              than an unfinished page. */}
          <Route
            path="/items/:itemId"
            element={<p className="empty">The item detail page is not built yet.</p>}
          />
          <Route path="/collections" element={<Collections />} />
          <Route path="/failures" element={<Failures />} />
          <Route path="/settings" element={<Settings />} />
          {/* The server serves index.html for any unclaimed path, so an
              unknown URL reaches the router rather than a 404 page. */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Gate() {
  const { authenticated } = useSession();
  return authenticated ? <AuthenticatedApp /> : <Login />;
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
