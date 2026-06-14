<script lang="ts">
  /**
   * SW update toast — "Actualización disponible — recargá" (T-008).
   *
   * Module 010 / Task T-008.
   *
   * Mounts at the app root; reacts to ``swUpdate.needRefresh``. Click
   * the CTA → activate the new SW + reload. Click dismiss → next cold
   * start picks it up.
   */
  import { swUpdate } from '../sw-update-notifier.svelte';

  async function apply(): Promise<void> {
    await swUpdate.applyAndReload();
  }
</script>

{#if swUpdate.needRefresh}
  <div class="toast" role="status" aria-live="polite">
    <span class="msg">Actualización disponible — recargá</span>
    <button type="button" class="cta" onclick={apply}>Recargar</button>
    <button
      type="button"
      class="dismiss"
      aria-label="Descartar"
      onclick={() => swUpdate.dismiss()}
    >
      ×
    </button>
  </div>
{/if}

<style>
  .toast {
    position: fixed;
    left: 50%;
    bottom: calc(
      var(--layout-bottomnav-height) + var(--layout-safe-bottom) +
        var(--space-3)
    );
    transform: translateX(-50%);
    display: inline-flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-full);
    background: var(--color-text);
    color: var(--color-text-inverse);
    box-shadow: var(--shadow-md);
    z-index: 30;
    font-size: var(--font-size-sm);
  }

  .msg {
    line-height: var(--line-height-tight);
  }

  .cta {
    padding: var(--space-1) var(--space-3);
    border: 0;
    border-radius: var(--radius-full);
    background: var(--color-accent);
    color: var(--color-accent-text);
    font: inherit;
    font-weight: 500;
    cursor: pointer;
  }

  .dismiss {
    border: 0;
    background: transparent;
    color: var(--color-text-inverse);
    font-size: var(--font-size-lg);
    line-height: 1;
    cursor: pointer;
    padding: 0 var(--space-1);
  }
</style>
