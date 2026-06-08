import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";

// Vite config for the AI Plot Twist PWA client.
//
// The service-worker and PWA-manifest plumbing ships in T-016 — this file
// currently just wires up Svelte 5 compilation and the dev server.
//
// strictPort=true makes the dev server fail loudly when 5173 is already in
// use (spec §"Edge Cases": port collisions must NOT silently fall back).

export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    strictPort: true,
  },
});
