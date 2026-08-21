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
    // The dev server runs inside the compose network (docker-compose.yml's
    // `web` service), so the API is reachable by service name. Only used in
    // development -- in the container the API and the built assets are the
    // same origin, so no proxy exists at runtime.
    //
    // This proxied localhost:8000 for two phases, which no process ever
    // listened on: main.py binds 8080. The dev server forwarded every /api
    // call into nothing and the config file looked entirely reasonable, so
    // tests/test_dev_environment.py now asserts the two agree.
    proxy: {
      "/api": "http://api:8080",
      "/healthz": "http://api:8080",
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
