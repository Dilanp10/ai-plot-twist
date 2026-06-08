/**
 * Svelte 5 application entry point.
 *
 * Uses the `mount()` API (Svelte 5) instead of `new App({target})` (Svelte 4).
 * `mount()` is composable, lazy, and supports SSR hydration — it's the only
 * recommended entry pattern in Svelte 5.
 */
import { mount } from "svelte";
import App from "./App.svelte";

const target = document.getElementById("app");
if (target === null) {
  // This should never happen with the correct index.html,
  // but fail loudly if it does so the error is obvious.
  throw new Error('No se encontró el elemento con id "app". Verificá index.html.');
}

mount(App, { target });
