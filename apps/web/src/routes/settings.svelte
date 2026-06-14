<script lang="ts">
  /**
   * /settings — preferences + sign-out (FR-005).
   *
   * Module 010 / Task T-007.
   *
   * Surfaces:
   *   - Display name (read-only).
   *   - Notifications toggle — UI stub. Real plumbing lands with
   *     module 011 (web-push); we honor the browser's
   *     ``Notification.permission`` state for display only.
   *   - App version.
   *   - Sign-out — with a Spanish confirmation dialog (FR-005).
   */
  import { authStore } from '../lib/auth-store.svelte';
  import { signOut } from '../lib/sign-out';
  import { S } from '../lib/strings';
  import { VERSION } from '../lib/version';

  // Browsers without Notification API (older iOS Safari, in-app browsers)
  // never expose Notification — fall back to "default" so the toggle
  // renders as off rather than crashing.
  function permState(): 'default' | 'granted' | 'denied' {
    if (typeof Notification === 'undefined') return 'default';
    return Notification.permission;
  }

  let notifGranted = $state<boolean>(permState() === 'granted');
  let confirming = $state(false);
  let busy = $state(false);

  async function onNotifChange(): Promise<void> {
    // T-007 ships only the toggle UX. The real subscribe / unsubscribe
    // flow is wired in module 011 (T-005..T-009 of that module).
    if (typeof Notification === 'undefined') return;
    if (notifGranted && Notification.permission !== 'granted') {
      const result = await Notification.requestPermission();
      notifGranted = result === 'granted';
    }
  }

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

  <label class="row toggle">
    <span class="label">
      <span class="label-main">{S.settings.notifications}</span>
      <span class="label-hint">{S.settings.notificationsHint}</span>
    </span>
    <input
      type="checkbox"
      bind:checked={notifGranted}
      onchange={onNotifChange}
      aria-label={S.settings.notifications}
    />
  </label>

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

  .toggle {
    cursor: pointer;
  }

  .label {
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
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
