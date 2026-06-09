<script lang="ts">
  /**
   * Placeholder /today route — authenticated user home.
   *
   * Module 002 / Task T-022.
   *
   * Displayed after successful login or when a returning user already
   * has a valid JWT in IndexedDB.  Future modules will replace this
   * placeholder with the daily narrative screen.
   */
  import { authStore } from '../lib/auth-store.svelte';
</script>

<main class="today">
  <h1>AI Plot Twist</h1>
  {#if authStore.user}
    <p class="welcome">Bienvenido/a, <strong>{authStore.user.display_name}</strong>.</p>
  {:else}
    <p class="welcome">Bienvenido/a.</p>
  {/if}
  <p class="placeholder">La historia está por comenzar…</p>
</main>

<style>
  .today {
    max-width: 600px;
    margin: 4rem auto;
    padding: 2rem;
    text-align: center;
    font-family: system-ui, sans-serif;
  }

  h1 {
    font-size: 2rem;
    font-weight: 700;
    color: #1a1a2e;
  }

  .welcome {
    font-size: 1.1rem;
    color: #444;
    margin-top: 1rem;
  }

  .placeholder {
    color: #999;
    font-style: italic;
    margin-top: 0.5rem;
  }
</style>
