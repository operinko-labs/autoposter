import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    // The Dockerfile copies this directory out of the Node stage, and
    // src/autoposter/api/spa.py serves it. Both hard-code "dist".
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    // `npm run dev` talks to a locally running `uvicorn autoposter.main:app`.
    // Only used in development -- in the container the API and the built
    // assets are the same origin, so no proxy exists at runtime.
    proxy: {
      "/api": "http://localhost:8000",
      "/healthz": "http://localhost:8000",
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    css: false,
    // vi.restoreAllMocks() does not undo vi.stubGlobal, so without this a
    // `fetch` or `localStorage` stub set by one test is still in place for
    // the next one -- including the next test that meant to assert against
    // the guard in test-setup.ts.
    unstubGlobals: true,
  },
});
