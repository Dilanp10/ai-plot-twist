<script lang="ts">
  /**
   * Error boundary — catches uncaught errors thrown during child render.
   *
   * Module 010 / Task T-010.
   *
   * Svelte 5 doesn't ship a React-style class-based boundary; we use the
   * runtime's :class:`Svelte.boundary` block instead. When a child throws,
   * the ``failed`` snippet renders, the error is reported via
   * :func:`logBoundary`, and the user gets a refresh button.
   *
   * Wraps each route's main view (FR-009). The fallback UI is intentionally
   * minimal: a title, a body, a primary action. Detailed error info goes
   * to ``/internal/client-log`` — never on screen.
   *
   * Slot:
   *   - default — the protected subtree.
   */
  import { logBoundary } from '../client-logger';
  import { S } from '../strings';

  interface Props {
    children?: import('svelte').Snippet;
  }
  const { children }: Props = $props();

  function report(error: unknown): void {
    const message =
      error instanceof Error
        ? error.message
        : typeof error === 'string'
          ? error
          : 'Unknown error';
    const stack = error instanceof Error ? error.stack : undefined;
    void logBoundary({ message, stack });
  }

  function refresh(): void {
    window.location.reload();
  }
</script>

<svelte:boundary onerror={report}>
  {#if children}{@render children()}{/if}

  {#snippet failed(_error, _reset)}
    <section class="boundary" role="alert" aria-live="assertive">
      <h2 class="title">{S.errors.boundaryTitle}</h2>
      <p class="body">{S.errors.boundaryBody}</p>
      <button type="button" class="retry" onclick={refresh}>
        {S.errors.boundaryRetry}
      </button>
    </section>
  {/snippet}
</svelte:boundary>

<style>
  .boundary {
    margin: var(--space-5) var(--space-4);
    padding: var(--space-5);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    text-align: center;
  }

  .title {
    margin: 0 0 var(--space-3);
    font-size: var(--font-size-lg);
    color: var(--color-text);
  }

  .body {
    margin: 0 0 var(--space-4);
    color: var(--color-text-muted);
    font-size: var(--font-size-md);
  }

  .retry {
    padding: var(--space-3) var(--space-5);
    border: 0;
    border-radius: var(--radius-md);
    background: var(--color-accent);
    color: var(--color-accent-text);
    font: inherit;
    font-weight: 500;
    cursor: pointer;
  }

  .retry:hover,
  .retry:focus-visible {
    background: var(--color-accent-hover);
  }
</style>
