<script lang="ts">
  /**
   * Android install card — appears once a deferred prompt is available
   * AND the user has viewed the today screen at least once (FR-004).
   *
   * Module 010 / Task T-005.
   *
   * The "viewed at least one chapter" gate is enforced by the parent
   * (Today screen) — it only mounts this card after chapterStore loads
   * with ``status === 'ok'``.
   */
  import { installPrompt } from '../install-prompt.svelte';
  import { S } from '../strings';

  let visible = $state(true);

  async function install(): Promise<void> {
    const outcome = await installPrompt.prompt();
    // Outcome is 'accepted' | 'dismissed' | 'noop'. Either way: hide.
    visible = false;
    if (outcome === 'dismissed') {
      // Remember the dismissal for this session so we don't re-render
      // the card if the user navigates Today → Vote → Today.
      sessionStorage.setItem('install-prompt-dismissed', '1');
    }
  }

  function dismiss(): void {
    installPrompt.dismiss();
    visible = false;
    sessionStorage.setItem('install-prompt-dismissed', '1');
  }

  const alreadyDismissed = $derived(
    typeof sessionStorage !== 'undefined' &&
      sessionStorage.getItem('install-prompt-dismissed') === '1',
  );
</script>

{#if visible && installPrompt.canPrompt && !alreadyDismissed}
  <aside class="card" aria-label={S.install.androidTitle}>
    <p class="title">{S.install.androidTitle}</p>
    <p class="body">{S.install.androidBody}</p>
    <div class="actions">
      <button type="button" class="dismiss" onclick={dismiss}>
        {S.install.androidDismiss}
      </button>
      <button type="button" class="install" onclick={install}>
        {S.install.androidCta}
      </button>
    </div>
  </aside>
{/if}

<style>
  .card {
    margin: var(--space-4) var(--space-4) var(--space-5);
    padding: var(--space-4);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    box-shadow: var(--shadow-sm);
  }

  .title {
    margin: 0 0 var(--space-2);
    font-size: var(--font-size-md);
    font-weight: 600;
    color: var(--color-text);
  }

  .body {
    margin: 0 0 var(--space-4);
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
  }

  .actions {
    display: flex;
    gap: var(--space-3);
    justify-content: flex-end;
  }

  .dismiss,
  .install {
    padding: var(--space-2) var(--space-4);
    border: 0;
    border-radius: var(--radius-md);
    font: inherit;
    font-weight: 500;
    cursor: pointer;
  }

  .dismiss {
    background: transparent;
    color: var(--color-text-muted);
  }

  .install {
    background: var(--color-accent);
    color: var(--color-accent-text);
  }

  .install:hover,
  .install:focus-visible {
    background: var(--color-accent-hover);
  }
</style>
