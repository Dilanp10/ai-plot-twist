import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/vite-plugin-svelte').SvelteConfig} */
const config = {
  // vitePreprocess lets the Svelte compiler understand TypeScript and CSS
  // inside <script lang="ts"> and <style> blocks.
  preprocess: vitePreprocess(),
};

export default config;
