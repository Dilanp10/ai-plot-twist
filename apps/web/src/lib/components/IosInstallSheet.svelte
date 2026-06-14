<script lang="ts">
  /**
   * iOS install instructions sheet (T-006).
   *
   * Module 010 / Task T-006.
   *
   * Only renders when ``detectIosInstallState() === 'show_instructions'``.
   * The in-app-browser variant is also handled — we surface a different
   * copy because install is impossible from inside Instagram / Twitter
   * etc.
   *
   * Dismissal is remembered per-session via sessionStorage so the user
   * doesn't see the sheet on every route change.
   */
  import { detectIosInstallState } from '../ios-install-sheet';
  import { S } from '../strings';

  const installState = detectIosInstallState();

  let dismissed = $state(
    typeof sessionStorage !== 'undefined' &&
      sessionStorage.getItem('ios-install-dismissed') === '1',
  );

  function dismiss(): void {
    dismissed = true;
    sessionStorage.setItem('ios-install-dismissed', '1');
  }
</script>

{#if !dismissed && installState === 'show_instructions'}
  <aside class="sheet" aria-label={S.install.iosTitle}>
    <h2 class="title">{S.install.iosTitle}</h2>
    <ol class="steps">
      <li>{S.install.iosStep1}</li>
      <li>{S.install.iosStep2}</li>
    </ol>
    <button type="button" class="dismiss" onclick={dismiss}>
      {S.install.iosDismiss}
    </button>
  </aside>
{:else if !dismissed && installState === 'in_app_browser'}
  <aside class="sheet warning" role="alert">
    <p class="title">Abrí en Safari para instalar</p>
    <p class="body">
      Este navegador no permite instalar la app. Tocá el menú y elegí
      "Abrir en Safari".
    </p>
    <button type="button" class="dismiss" onclick={dismiss}>
      {S.install.iosDismiss}
    </button>
  </aside>
{/if}

<style>
  .sheet {
    position: fixed;
    left: var(--space-3);
    right: var(--space-3);
    bottom: calc(
      var(--layout-bottomnav-height) + var(--layout-safe-bottom) +
        var(--space-3)
    );
    max-width: var(--layout-max-width);
    margin: 0 auto;
    padding: var(--space-4);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
    box-shadow: var(--shadow-md);
    z-index: 20;
  }

  .sheet.warning {
    border-color: var(--color-warning);
  }

  .title {
    margin: 0 0 var(--space-3);
    font-size: var(--font-size-md);
    font-weight: 600;
    color: var(--color-text);
  }

  .steps {
    margin: 0 0 var(--space-4);
    padding-left: var(--space-5);
    color: var(--color-text);
    font-size: var(--font-size-sm);
    line-height: var(--line-height-normal);
  }

  .steps li {
    margin: var(--space-1) 0;
  }

  .body {
    margin: 0 0 var(--space-4);
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
  }

  .dismiss {
    width: 100%;
    padding: var(--space-3);
    border: 0;
    border-radius: var(--radius-md);
    background: var(--color-surface-elevated);
    color: var(--color-text);
    font: inherit;
    font-weight: 500;
    cursor: pointer;
  }
</style>
