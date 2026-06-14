import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// Vite config for the AI Plot Twist PWA client.
//
// strictPort=true: dev server fails loudly when 5173 is already in use
//   (spec §"Edge Cases": port collisions must NOT silently fall back).
//
// VitePWA strategy "generateSW": vite-plugin-pwa generates the service
//   worker automatically from the workbox config. Module 010 will switch
//   to "injectManifest" for full custom SW logic; "generateSW" is the
//   correct default for bootstrapping a Lighthouse-installable PWA.

export default defineConfig({
  plugins: [
    svelte(),
    VitePWA({
      // "generateSW" lets Workbox manage the SW; module 010 switches to
      // "injectManifest" when custom SW logic (push, offline caching) ships.
      strategies: "generateSW",
      registerType: "autoUpdate",

      // Serve the manifest at /manifest.webmanifest (spec FR-009).
      manifest: {
        name: "AI Plot Twist",
        short_name: "Plot Twist",
        description: "Juego social-narrativo de ciclo diario.",
        lang: "es",
        start_url: "/",
        display: "standalone",
        background_color: "#1a1a2e",
        theme_color: "#1a1a2e",
        icons: [
          {
            src: "/icons/icon-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any maskable",
          },
        ],
      },

      workbox: {
        // Pre-cache everything in the build output.
        globPatterns: ["**/*.{js,css,html,ico,png,svg,webp,woff2}"],
        // Keep the runtime cache clean.
        cleanupOutdatedCaches: true,

        // Module 010 / T-004 — FR-003 runtime caching:
        //   /api/v1/chapters/today   → SWR, 10 min max age
        //   /api/v1/seasons/*        → SWR, 1 h
        //   R2 assets domain         → cacheFirst, 30 days
        //   anything else under /api → networkOnly (no cache)
        runtimeCaching: [
          {
            urlPattern: ({ url }) => url.pathname === "/api/v1/chapters/today",
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "api-chapters-today",
              expiration: {
                maxAgeSeconds: 10 * 60,
                maxEntries: 8,
              },
            },
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/v1/seasons/"),
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "api-seasons",
              expiration: {
                maxAgeSeconds: 60 * 60,
                maxEntries: 16,
              },
            },
          },
          {
            urlPattern: ({ url }) =>
              url.host === "assets.aiplottwist.example",
            handler: "CacheFirst",
            options: {
              cacheName: "r2-assets",
              expiration: {
                maxAgeSeconds: 30 * 24 * 60 * 60,
                maxEntries: 200,
              },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
          {
            urlPattern: ({ url }) => url.pathname.startsWith("/api/"),
            handler: "NetworkOnly",
          },
        ],
      },

      // Suppress the "PWA assets are not optimized" warning during dev.
      devOptions: {
        enabled: true,
        type: "module",
      },
    }),
  ],
  server: {
    port: 5173,
    strictPort: true,
  },
});
