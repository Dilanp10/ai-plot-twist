<script lang="ts">
  /**
   * Top bar — app name + cycle-state badge + chapter day index.
   *
   * Module 010 / Task T-003.
   *
   * Reads ``chapterStore`` reactively. When the store has no chapter yet
   * (loading / no_season / no_release / error), the badge is hidden.
   *
   * Visual contract:
   *   - left:    app name
   *   - right:   "Día N · <badge>" (when data is loaded)
   *
   * The badge color is driven by the ``--color-state-*`` tokens declared
   * in ``theme-tokens.css``.
   */
  import { chapterStore, type CycleState } from '../chapter-store.svelte';
  import { S } from '../strings';

  function badgeLabel(state: CycleState): string {
    switch (state) {
      case 'ESTRENO':
        return S.states.estreno;
      case 'RECEPCION_IDEAS':
        return S.states.recepcionIdeas;
      case 'FILTERING':
        return S.states.filtering;
      case 'VOTACION':
        return S.states.votacion;
      case 'GENERACION':
        return S.states.generacion;
      case 'PENDING_RELEASE':
        return S.states.pendingRelease;
      case 'FAILED':
        return S.states.failed;
    }
  }

  function badgeColorVar(state: CycleState): string {
    switch (state) {
      case 'ESTRENO':
        return 'var(--color-state-estreno)';
      case 'RECEPCION_IDEAS':
        return 'var(--color-state-recepcion)';
      case 'FILTERING':
        return 'var(--color-state-filtering)';
      case 'VOTACION':
        return 'var(--color-state-votacion)';
      case 'GENERACION':
        return 'var(--color-state-generacion)';
      case 'PENDING_RELEASE':
        return 'var(--color-state-pending)';
      case 'FAILED':
        return 'var(--color-state-failed)';
    }
  }

  const showMeta = $derived(
    chapterStore.status === 'ok' && chapterStore.data !== null,
  );
</script>

<header class="top-bar">
  <span class="brand">{S.appShell.appName}</span>

  {#if showMeta && chapterStore.data}
    <span class="meta">
      <span class="day">Día {chapterStore.data.chapter.day_index}</span>
      <span
        class="badge"
        style:background-color={badgeColorVar(chapterStore.data.cycle_state)}
      >
        {badgeLabel(chapterStore.data.cycle_state)}
      </span>
    </span>
  {/if}
</header>

<style>
  .top-bar {
    position: sticky;
    top: 0;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: calc(var(--layout-topbar-height) + var(--layout-safe-top));
    padding: var(--layout-safe-top) var(--space-4) 0;
    background: rgba(10, 10, 15, 0.85);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--color-border);
  }

  .brand {
    background: linear-gradient(
      135deg,
      var(--color-accent) 0%,
      #c084fc 100%
    );
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.02em;
  }

  .brand {
    font-family: var(--font-display);
    font-size: var(--font-size-lg);
    font-weight: 700;
  }

  .meta {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--font-size-sm);
  }

  .day {
    color: var(--color-text-muted);
  }

  .badge {
    color: var(--color-accent-text);
    font-weight: 500;
    padding: 2px var(--space-2);
    border-radius: var(--radius-full);
    line-height: var(--line-height-tight);
  }
</style>
