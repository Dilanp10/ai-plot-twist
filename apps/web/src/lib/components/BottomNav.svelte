<script lang="ts">
  /**
   * Bottom tab navigation — 4 destinations.
   *
   * Module 010 / Task T-003.
   *
   * Tabs: Today / Vote / Me / Settings (per FR-001).
   * The active tab is computed from ``router.current`` so navigation
   * via either tap or programmatic ``router.navigate`` keeps the UI
   * in sync.
   *
   * Accessibility:
   *   - <nav> landmark with aria-label
   *   - aria-current="page" on the active link
   *   - Min 44×44 px tap target (NFR a11y)
   */
  import { router } from '../router.svelte';
  import { S } from '../strings';

  interface Tab {
    path: string;
    label: string;
    icon: string;
  }

  const tabs: Tab[] = [
    { path: '/series', label: S.appShell.nav.series, icon: '◈' },
    { path: '/today', label: S.appShell.nav.today, icon: '◌' },
    { path: '/me', label: S.appShell.nav.me, icon: '✎' },
    { path: '/settings', label: S.appShell.nav.settings, icon: '⚙' },
  ];

  function isActive(path: string): boolean {
    if (path === '/series') {
      return router.current === '/series' || router.current.startsWith('/chapter/');
    }
    return router.current === path;
  }

  function go(e: MouseEvent, path: string): void {
    e.preventDefault();
    router.navigate(path);
  }
</script>

<nav class="bottom-nav" aria-label="Navegación principal">
  {#each tabs as tab (tab.path)}
    <a
      href="#{tab.path}"
      class="tab"
      class:active={isActive(tab.path)}
      aria-current={isActive(tab.path) ? 'page' : undefined}
      onclick={(e) => go(e, tab.path)}
    >
      <span class="icon" aria-hidden="true">{tab.icon}</span>
      <span class="label">{tab.label}</span>
    </a>
  {/each}
</nav>

<style>
  .bottom-nav {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    display: flex;
    justify-content: stretch;
    align-items: stretch;
    height: calc(var(--layout-bottomnav-height) + var(--layout-safe-bottom));
    padding-bottom: var(--layout-safe-bottom);
    background: rgba(10, 10, 15, 0.9);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-top: 1px solid var(--color-border);
    z-index: 10;
  }

  .tab.active .icon {
    transform: scale(1.15);
  }

  .icon {
    transition: transform var(--motion-fast) var(--motion-easing);
  }

  .tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    text-decoration: none;
    color: var(--color-text-muted);
    min-height: 44px;
    transition: color var(--motion-fast) var(--motion-easing);
  }

  .tab:hover,
  .tab:focus-visible {
    color: var(--color-text);
  }

  .tab.active {
    color: var(--color-accent);
  }

  .icon {
    font-size: var(--font-size-lg);
    line-height: 1;
  }

  .label {
    font-size: var(--font-size-xs);
    line-height: var(--line-height-tight);
  }
</style>
