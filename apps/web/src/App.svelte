<script lang="ts">
  /**
   * Root application component — SPA router shell.
   *
   * Module 002 / Task T-022.
   *
   * Boot sequence:
   *   1. Calls authStore.init() to load the JWT from IndexedDB.
   *   2. Routes to /today if authenticated, else /onboarding.
   *
   * Window events handled:
   *   auth:navigate  – navigates to event.detail.path (fired by child routes)
   *   auth:logout    – navigates to /onboarding (fired by the API interceptor)
   */
  import { onMount } from 'svelte';
  import { authStore } from './lib/auth-store.svelte';
  import { router } from './lib/router.svelte';
  import Onboarding from './routes/onboarding.svelte';
  import Today from './routes/today.svelte';

  /** Tracks whether the async boot sequence has completed. */
  let initialized = $state(false);

  onMount(() => {
    const onNavigate = (e: Event) => {
      router.navigate((e as CustomEvent<{ path: string }>).detail.path);
    };
    const onLogout = () => router.navigate('/onboarding');

    window.addEventListener('auth:navigate', onNavigate);
    window.addEventListener('auth:logout', onLogout);

    // Boot: load persisted JWT, then route accordingly.
    void authStore.init().then(() => {
      router.navigate(authStore.jwt ? '/today' : '/onboarding');
      initialized = true;
    });

    return () => {
      window.removeEventListener('auth:navigate', onNavigate);
      window.removeEventListener('auth:logout', onLogout);
    };
  });
</script>

{#if initialized}
  {#if router.current === '/today'}
    <Today />
  {:else}
    <Onboarding />
  {/if}
{/if}
