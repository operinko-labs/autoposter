import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./theme.css";

/** When the last automatic reload for a stale chunk happened, per tab. */
const CHUNK_RELOAD_KEY = "autoposter.chunkReloadAt";
/** A reload that lands on a server still missing the chunk must not reload
 * again straight away; this long is "straight away". */
const CHUNK_RELOAD_GUARD_MS = 10_000;

/* Every page is a lazy chunk with a content hash in its name (perf spec A3).
 * A deploy replaces them all, so a tab still running the previous entry asks
 * for chunk names the new server no longer has, the first time it opens a
 * page it has not visited yet. Vite reports that as `vite:preloadError`;
 * reloading fetches the new entry, whose chunk names do exist.
 *
 * At most one reload per guard window, remembered in sessionStorage because
 * the reload itself wipes everything else: a chunk that is missing for some
 * other reason must end at the page's error boundary, not in a reload loop.
 * Without sessionStorage (blocked by a privacy setting) there is no way to
 * know, so nothing is reloaded. In every no-reload case the event is left
 * alone, so the import still rejects and the error boundary shows it. */
window.addEventListener("vite:preloadError", (event) => {
  try {
    const last = Number(window.sessionStorage.getItem(CHUNK_RELOAD_KEY) ?? 0);
    if (Date.now() - last < CHUNK_RELOAD_GUARD_MS) return;
    window.sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()));
  } catch {
    return;
  }
  event.preventDefault();
  window.location.reload();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
