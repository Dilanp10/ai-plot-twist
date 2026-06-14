<script lang="ts">
  /**
   * Root application component — SPA router shell.
   *
   * Module 010 / Task T-003 (rewires module 002 T-022 to mount AppShell
   * for authed routes; onboarding stays bare to own the viewport).
   *
   * Boot sequence:
   *   1. Calls authStore.init() to load the JWT from IndexedDB.
   *   2. Routes to /today (or honors deep-link hash) if authenticated,
   *      else /onboarding.
   *
   * Window events handled:
   *   auth:navigate  – navigates to event.detail.path (fired by child routes)
   *   auth:logout    – navigates to /onboarding (fired by API interceptor +
   *                    the Settings sign-out button)
   */
  import './lib/theme-tokens.css';
  import { onMount } from 'svelte';
  import { authStore } from './lib/auth-store.svelte';
  import AppShell from './lib/components/AppShell.svelte';
  import ErrorBoundary from './lib/components/ErrorBoundary.svelte';
  import IosInstallSheet from './lib/components/IosInstallSheet.svelte';
  import SwUpdateToast from './lib/components/SwUpdateToast.svelte';
  import { router } from './lib/router.svelte';
  import Me from './routes/me.svelte';
  import Onboarding from './routes/onboarding.svelte';
  import Settings from './routes/settings.svelte';
  import Today from './routes/today.svelte';
  import Vote from './routes/vote.svelte';

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
      if (!authStore.jwt) {
        router.navigate('/onboarding');
      } else {
        // Honor a deep-link hash when present; otherwise default to /today.
        router.syncFromHash();
        if (router.current === '/' || router.current === '/onboarding') {
          router.navigate('/today');
        }
      }
      initialized = true;
    });

    return () => {
      window.removeEventListener('auth:navigate', onNavigate);
      window.removeEventListener('auth:logout', onLogout);
    };
  });
</script>

{#if initialized}
  {#if router.current === '/onboarding'}
    <ErrorBoundary>
      <Onboarding />
    </ErrorBoundary>
  {:else}
    <AppShell>
      <ErrorBoundary>
        {#if router.current === '/vote'}
          <Vote />
        {:else if router.current === '/me'}
          <Me />
        {:else if router.current === '/settings'}
          <Settings />
        {:else}
          <Today />
        {/if}
      </ErrorBoundary>
    </AppShell>
    <IosInstallSheet />
    <SwUpdateToast />
  {/if}
{/if}
