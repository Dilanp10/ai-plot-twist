<script lang="ts">
  /**
   * Tiny wrapper that lazy-loads a route component via dynamic import.
   *
   * Module 010 / Task T-014.
   *
   * Usage::
   *
   *     <LazyRoute loader={() => import('../../routes/vote.svelte')} />
   *
   * Vite chunk-splits the dynamic import into its own JS file, so the
   * vote / me / settings code only ships to clients that actually
   * navigate there. The Today route (most common destination after
   * onboarding) stays eager-loaded so its first paint isn't blocked
   * on a chunk fetch.
   *
   * While the chunk is in flight we render :class:`Skeleton` blocks
   * matching the route's general shape — short enough to not flash
   * but visible enough to show progress on slow networks.
   *
   * On load failure (offline, deploy mismatch), the surrounding
   * :class:`ErrorBoundary` catches the rejection so the user gets a
   * refresh CTA instead of a blank screen.
   */
  import type { Component } from 'svelte';
  import Skeleton from './Skeleton.svelte';

  interface Props {
    loader: () => Promise<{ default: Component }>;
  }
  const { loader }: Props = $props();

  // Re-evaluate the loader whenever it changes — different routes pass
  // different loader closures, so we want a fresh import each switch.
  const modulePromise = $derived(loader());
</script>

{#await modulePromise}
  <section class="lazy-loading" aria-busy="true">
    <Skeleton height="2rem" width="60%" />
    <Skeleton height="1rem" width="40%" />
    <Skeleton height="6rem" radius="var(--radius-md)" />
  </section>
{:then mod}
  {@const RouteComponent = mod.default}
  <RouteComponent />
{/await}

<style>
  .lazy-loading {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
  }
</style>
