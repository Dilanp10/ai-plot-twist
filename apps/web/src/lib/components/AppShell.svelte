<script lang="ts">
  /**
   * App shell — TopBar + scrollable content slot + BottomNav.
   *
   * Module 010 / Task T-003.
   *
   * The shell is mounted by the root ``App.svelte`` whenever the user is
   * authenticated. Unauthed flows (onboarding) bypass the shell so the
   * code-input screen owns the viewport.
   *
   * Layout:
   *   ┌──────────────────────────┐
   *   │ TopBar  (sticky, 56 px) │
   *   ├──────────────────────────┤
   *   │ <slot> (scroll area)    │
   *   │                          │
   *   ├──────────────────────────┤
   *   │ BottomNav  (fixed, 64 px)│
   *   └──────────────────────────┘
   */
  import BottomNav from './BottomNav.svelte';
  import TopBar from './TopBar.svelte';

  interface Props {
    children?: import('svelte').Snippet;
  }
  const { children }: Props = $props();
</script>

<div class="shell">
  <TopBar />
  <main class="content">
    {#if children}{@render children()}{/if}
  </main>
  <BottomNav />
</div>

<style>
  .shell {
    display: flex;
    flex-direction: column;
    min-height: 100dvh;
    background: var(--color-bg);
    color: var(--color-text);
    font-family: var(--font-body);
  }

  .content {
    flex: 1;
    padding-bottom: calc(
      var(--layout-bottomnav-height) + var(--layout-safe-bottom) +
        var(--space-4)
    );
    max-width: var(--layout-max-width);
    margin: 0 auto;
    width: 100%;
  }
</style>
