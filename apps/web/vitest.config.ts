import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vitest/config";

// Vitest config for the AI Plot Twist web client.
//
// Uses jsdom so Svelte components can mount against a DOM-like environment
// without a real browser. @testing-library/svelte wraps component mounting
// and provides query helpers (getByText, etc.).

export default defineConfig({
  plugins: [svelte()],

  // Force Vite to prefer the "browser" export condition so Svelte 5 resolves
  // to its client build (index-client.js) rather than the server build
  // (index-server.js). Without this, jsdom's Node environment causes Svelte
  // to pick the server bundle, which throws "mount() is not available on the
  // server" even in a jsdom context.
  resolve: {
    conditions: ["browser"],
  },

  test: {
    // Browser-like environment required by Svelte 5 mount() and by the
    // DOM queries in @testing-library/svelte.
    environment: "jsdom",

    // Auto-import vitest globals (describe, it, expect, …) so test files
    // don't need explicit `import { test, expect } from "vitest"`.
    globals: true,

    // Run the @testing-library/svelte cleanup hook after each test so
    // mounted components don't leak across test cases.
    setupFiles: ["./src/test-setup.ts"],

    // Exclude Playwright E2E specs — they run via `pnpm test:e2e`, not vitest.
    exclude: ["**/e2e/**", "**/node_modules/**"],
  },
});
