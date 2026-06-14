/**
 * Svelte 5 application entry point.
 *
 * Uses the `mount()` API (Svelte 5) instead of `new App({target})` (Svelte 4).
 * `mount()` is composable, lazy, and supports SSR hydration — it's the only
 * recommended entry pattern in Svelte 5.
 */
import { mount } from "svelte";
import App from "./App.svelte";
import { installGlobalHandlers } from "./lib/client-logger";
import { installBeforeInstallPromptListener } from "./lib/install-prompt.svelte";
import { reportSwUpdateAvailable } from "./lib/sw-update-notifier.svelte";

// Module 010 / T-011: forward window-level errors + unhandled rejections
// to /internal/client-log (throttled to 10/min on the client).
installGlobalHandlers();

// Module 010 / T-005: capture beforeinstallprompt before Chrome's
// mini-bar can fire so the in-app card can offer it on demand.
installBeforeInstallPromptListener();

// Module 010 / T-004 + T-008: register the workbox-generated SW and
// bridge its needRefresh callback to the SwUpdateToast. The
// virtual:pwa-register module is provided by vite-plugin-pwa; we
// import it lazily so unit tests (which never bundle through vite)
// don't trip on the missing module.
if (import.meta.env.PROD) {
  void import("virtual:pwa-register").then(({ registerSW }) => {
    const updateSW = registerSW({
      onNeedRefresh() {
        reportSwUpdateAvailable(async () => {
          await updateSW(true);
        });
      },
    });
  });
}

const target = document.getElementById("app");
if (target === null) {
  // This should never happen with the correct index.html,
  // but fail loudly if it does so the error is obvious.
  throw new Error('No se encontró el elemento con id "app". Verificá index.html.');
}

mount(App, { target });
