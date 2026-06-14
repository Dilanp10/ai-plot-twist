<script lang="ts">
  /**
   * /settings — preferences + sign-out (placeholder).
   *
   * Module 010 / Task T-003 (placeholder for T-007 full impl).
   *
   * Shows the bare-minimum surface so the BottomNav tab resolves:
   *   - display name (read-only)
   *   - invite code masked
   *   - sign-out button (auth-store.signOut)
   *
   * T-007 will add the notifications toggle, version line, and the
   * confirmation dialog. T-008 hooks the SW update notifier.
   */
  import { authStore } from '../lib/auth-store.svelte';
  import { S } from '../lib/strings';

  async function signOut(): Promise<void> {
    await authStore.clear();
    window.dispatchEvent(new Event('auth:logout'));
  }
</script>

<section class="screen">
  <h1 class="title">{S.settings.title}</h1>

  <div class="row">
    <span class="label">{S.settings.displayName}</span>
    <span class="value">{authStore.user?.display_name ?? '—'}</span>
  </div>

  <button type="button" class="signout" onclick={signOut}>
    {S.settings.signOut}
  </button>
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

  .label {
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
  }

  .value {
    color: var(--color-text);
    font-size: var(--font-size-md);
    font-weight: 500;
  }

  .signout {
    margin-top: var(--space-6);
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
</style>
