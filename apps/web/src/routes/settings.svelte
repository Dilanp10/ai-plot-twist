<script lang="ts">
  /**
   * /settings — preferences + sign-out (FR-005).
   *
   * Module 010 / Task T-007.  Push toggle wired in module 011 / T-012.
   *
   * Surfaces:
   *   - Display name (read-only).
   *   - Notifications toggle — PushToggle component (module 011).
   *   - App version.
   *   - Sign-out — with a Spanish confirmation dialog (FR-005).
   */
  import { onMount } from 'svelte';
  import { authStore } from '../lib/auth-store.svelte';
  import PushToggle from '../lib/components/PushToggle.svelte';
  import { pushStore } from '../lib/push-store.svelte';
  import { signOut } from '../lib/sign-out';
  import { S } from '../lib/strings';
  import { VERSION } from '../lib/version';

  let confirming = $state(false);
  let busy = $state(false);

  onMount(() => {
    void pushStore.init();
  });

  async function confirmSignOut(): Promise<void> {
    busy = true;
    try {
      await signOut();
    } finally {
      busy = false;
      confirming = false;
    }
  }
</script>

<section class="screen">
  <h1 class="title">{S.settings.title}</h1>

  <div class="row">
    <span class="label">{S.settings.displayName}</span>
    <span class="value">{authStore.user?.display_name ?? '—'}</span>
  </div>

  <PushToggle />

  <p class="version">{S.settings.appVersion(VERSION)}</p>

  {#if !confirming}
    <button
      type="button"
      class="signout"
      onclick={() => (confirming = true)}
      disabled={busy}
    >
      {S.settings.signOut}
    </button>
  {:else}
    <div class="confirm" role="alertdialog" aria-modal="true">
      <p class="confirm-body">{S.settings.signOutConfirm}</p>
      <div class="confirm-row">
        <button
          type="button"
          class="confirm-cancel"
          onclick={() => (confirming = false)}
          disabled={busy}
        >
          Cancelar
        </button>
        <button
          type="button"
          class="confirm-ok"
          onclick={confirmSignOut}
          disabled={busy}
        >
          {S.settings.signOut}
        </button>
      </div>
    </div>
  {/if}
</section>

<style>
  .screen {
    padding: var(--space-4);
  }

  .title {
    font-size: var(--font-size-xl);
    font-weight: 600;
    margin: 0 0 var(--space-5);
    color: var(--color-text);
  }

  .row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: var(--space-3) 0;
    border-bottom: 1px solid var(--color-border);
  }

  .value {
    color: var(--color-text);
    font-size: var(--font-size-md);
    font-weight: 500;
  }

  .version {
    margin: var(--space-5) 0;
    color: var(--color-text-muted);
    font-size: var(--font-size-xs);
    text-align: center;
  }

  .signout {
    margin-top: var(--space-2);
    width: 100%;
    padding: var(--space-3) var(--space-4);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    color: var(--color-danger);
    font: inherit;
    font-weight: 500;
    cursor: pointer;
  }

  .signout:hover,
  .signout:focus-visible {
    background: var(--color-surface-elevated);
  }

  .confirm {
    margin-top: var(--space-4);
    padding: var(--space-4);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }

  .confirm-body {
    margin: 0 0 var(--space-4);
    color: var(--color-text);
    font-size: var(--font-size-md);
  }

  .confirm-row {
    display: flex;
    gap: var(--space-3);
  }

  .confirm-cancel,
  .confirm-ok {
    flex: 1;
    padding: var(--space-3);
    border: 0;
    border-radius: var(--radius-md);
    font: inherit;
    font-weight: 500;
    cursor: pointer;
  }

  .confirm-cancel {
    background: var(--color-surface-elevated);
    color: var(--color-text);
  }

  .confirm-ok {
    background: var(--color-danger);
    color: var(--color-text-inverse);
  }

  .confirm-cancel:disabled,
  .confirm-ok:disabled,
  .signout:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
