<script lang="ts">
  /**
   * Placeholder home page — Bootstrap OK.
   *
   * Svelte 5 runes are opt-in per-component via `<script lang="ts">`.
   * This component has no reactive state, but demonstrates the runes
   * pattern: $props() is how Svelte 5 declares typed component props.
   * No props are declared here — the root App never receives any.
   */
  import { VERSION } from "./lib/version";

  // Svelte 5 runes note: $props() is the rune for declaring component
  // props (replaces `export let` from Svelte 4). This root component
  // receives no props, so $props() is omitted. Modules 010+ will use
  // it when building stateful components.
</script>

<main>
  <h1>AI Plot Twist</h1>
  <p class="tagline">Hola, esto es AI Plot Twist — bootstrap OK 🎬</p>
  <p class="version">v{VERSION}</p>
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 560px;
    margin: 4rem auto;
    padding: 2rem;
    text-align: center;
  }

  h1 {
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 0.5rem;
    color: #1a1a2e;
  }

  .tagline {
    font-size: 1.1rem;
    color: #444;
    margin-bottom: 0.25rem;
  }

  .version {
    font-size: 0.8rem;
    color: #999;
    font-family: monospace;
  }
</style>
