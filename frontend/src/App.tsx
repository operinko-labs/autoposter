import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { SessionProvider, useSession } from "./auth/SessionContext";
import { ActionCenter } from "./pages/ActionCenter";
import { Collections } from "./pages/Collections";
import { Dashboard } from "./pages/Dashboard";
import { Failures } from "./pages/Failures";
import { ItemDetail } from "./pages/ItemDetail";
import { Jobs } from "./pages/Jobs";
import { Library } from "./pages/Library";
import { Login } from "./pages/Login";
import { Logs } from "./pages/Logs";
import { Mismatches } from "./pages/Mismatches";
import { Modes } from "./pages/Modes";
import { Settings } from "./pages/Settings";
import { Testing } from "./pages/Testing";
import { Sidebar } from "./shell/Sidebar";
import "./shell/shell.css";

function AuthenticatedApp() {
  return (
    <div className="app-shell">
      <Sidebar />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/actions" element={<ActionCenter />} />
          <Route path="/library" element={<Library />} />
          <Route path="/items/:itemId" element={<ItemDetail />} />
          <Route path="/collections" element={<Collections />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/failures" element={<Failures />} />
          <Route path="/mismatches" element={<Mismatches />} />
          <Route path="/modes" element={<Modes />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/testing" element={<Testing />} />
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
