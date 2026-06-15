<script lang="ts">
  /**
   * Push notifications toggle row — module 011 / T-012.
   *
   * Three display states driven by pushStore:
   *   denied  → informational text, no action (browser blocked).
   *   enabled → "Desactivar" button (subscription active).
   *   default → "Activar" button (no subscription yet).
   *
   * The parent (settings.svelte) calls pushStore.init() in onMount
   * before rendering this component, so permission/subscription state
   * is already populated on first render.
   */
  import { pushStore } from '../push-store.svelte';
  import { S } from '../strings';

  let busy = $state(false);

  async function enable(): Promise<void> {
    busy = true;
    try {
      await pushStore.enable();
    } finally {
      busy = false;
    }
  }

  async function disable(): Promise<void> {
    busy = true;
    try {
      await pushStore.disable();
    } finally {
      busy = false;
    }
  }
</script>

<div class="row">
  <span class="label">
    <span class="label-main">{S.settings.notifications}</span>
    <span class="label-hint">{S.settings.notificationsHint}</span>
  </span>

  {#if pushStore.permission === 'denied'}
    <span class="badge denied">{S.push.blocked}</span>
  {:else if pushStore.serverKnowsAboutMe}
    <button
      type="button"
      class="btn btn-off"
      onclick={disable}
      disabled={busy}
      aria-pressed="true"
    >
      {busy ? S.push.saving : S.push.disable}
    </button>
  {:else}
    <button
      type="button"
      class="btn btn-on"
      onclick={enable}
      disabled={busy}
      aria-pressed="false"
    >
      {busy ? S.push.saving : S.push.enable}
    </button>
  {/if}
</div>

<style>
  .row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--space-3) 0;
    border-bottom: 1px solid var(--color-border);
  }

  .label {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .label-main {
    color: var(--color-text);
    font-size: var(--font-size-md);
    font-weight: 500;
  }

  .label-hint {
    color: var(--color-text-muted);
    font-size: var(--font-size-xs);
  }

  .badge {
    font-size: var(--font-size-xs);
    font-weight: 500;
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-sm);
  }

  .denied {
    background: var(--color-surface-elevated);
    color: var(--color-text-muted);
  }

  .btn {
    padding: var(--space-2) var(--space-3);
    border: 0;
    border-radius: var(--radius-md);
    font: inherit;
    font-size: var(--font-size-sm);
    font-weight: 500;
    cursor: pointer;
    min-width: 7rem;
    transition: opacity 0.15s;
  }

  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .btn-on {
    background: var(--color-accent);
    color: var(--color-accent-text);
  }

  .btn-on:hover:not(:disabled),
  .btn-on:focus-visible:not(:disabled) {
    background: var(--color-accent-hover);
  }

  .btn-off {
    background: var(--color-surface-elevated);
    color: var(--color-text-muted);
  }

  .btn-off:hover:not(:disabled),
  .btn-off:focus-visible:not(:disabled) {
    background: var(--color-border);
  }
</style>
