import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// Vite config for the AI Plot Twist PWA client.
//
// strictPort=true: dev server fails loudly when 5173 is already in use
//   (spec §"Edge Cases": port collisions must NOT silently fall back).
//
// VitePWA strategy "injectManifest": custom service-worker.ts handles push
// events and notification clicks (module 011 / T-013). Runtime caching
// rules that were previously in vite.config.ts workbox config are now
// defined inside the SW itself.

export default defineConfig({
  plugins: [
    svelte(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "service-worker.ts",
      registerType: "autoUpdate",

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

      injectManifest: {
        globPatterns: ["**/*.{js,css,html,ico,png,svg,webp,woff2}"],
      },

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
