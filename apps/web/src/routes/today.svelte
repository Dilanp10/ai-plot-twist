<script lang="ts">
  /**
   * Real /today route — daily chapter screen.
   *
   * Module 004 / Task T-014.
   *
   * Reads from ``chapterStore`` (T-012) and routes the layout by status:
   *   loading      → skeleton
   *   ok           → panels + narration + cliffhanger + state badge + Countdown
   *   maintenance  → maintenance screen with reason
   *   no_season    → "todavía no empezamos" empty state
   *   no_release   → countdown to first_release_at
   *   error        → generic fallback with retry button
   *
   * The CTA below the cliffhanger is a state-aware stub. Wiring lands with
   * modules 005 ("Tirá una idea") and 007 ("Votá las mejores").
   */
  import { onMount } from 'svelte';
  import { chapterStore, type CycleState } from '../lib/chapter-store.svelte';
  import { router } from '../lib/router.svelte';
  import { windowFor } from '../lib/window-countdown';
  import Countdown from '../lib/Countdown.svelte';
  import MyTwistsPanel from '../lib/components/MyTwistsPanel.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';
  import TwistModal from '../lib/components/TwistModal.svelte';

  let modalOpen = $state(false);

  onMount(() => {
    void chapterStore.load();
  });

  function isSubmitWindowOpen(submitUntilIso: string): boolean {
     
    return Date.now() < new Date(submitUntilIso).getTime();
  }

  function ctaForState(state: CycleState): string | null {
    switch (state) {
      case 'ESTRENO':
      case 'RECEPCION_IDEAS':
        return 'Tirá una idea';
      case 'FILTERING':
      case 'VOTACION':
        return 'Votá las mejores';
      case 'PENDING_RELEASE':
      case 'GENERACION':
      case 'FAILED':
        return null;
    }
  }

  function stateBadge(state: CycleState): string {
    switch (state) {
      case 'PENDING_RELEASE':
        return 'Próximo estreno';
      case 'ESTRENO':
      case 'RECEPCION_IDEAS':
        return 'Recepción de ideas';
      case 'FILTERING':
        return 'Filtrando ideas';
      case 'VOTACION':
        return 'Votación abierta';
      case 'GENERACION':
        return 'Generando capítulo';
      case 'FAILED':
        return 'En revisión';
    }
  }

  function formatFirstRelease(iso: string | null): string {
    if (!iso) return '';
    return new Date(iso).toLocaleString('es-AR', {
      weekday: 'long',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
</script>

<main class="today">
  {#if chapterStore.status === 'loading' || chapterStore.status === 'idle'}
    <section class="loading-skeleton" data-testid="loading">
      <Skeleton height="2rem" width="60%" />
      <Skeleton height="1rem" width="40%" />
      <Skeleton height="280px" radius="var(--radius-md)" />
      <Skeleton height="1rem" />
      <Skeleton height="1rem" width="80%" />
    </section>
  {:else if chapterStore.status === 'ok' && chapterStore.data}
    {@const dto = chapterStore.data}
    <header class="meta">
      <span class="badge" data-testid="state-badge">{stateBadge(dto.cycle_state)}</span>
      <h1>Ideas</h1>
      <p class="day">{dto.season.title} · Cap. {dto.chapter.day_index}</p>
    </header>

    {@const cd = windowFor(dto.cycle_state, dto.windows)}
    <div class="countdown-wrap">
      <Countdown label={cd.label} target={cd.target} />
    </div>

    {@const ctaLabel = ctaForState(dto.cycle_state)}
    {@const submitOpen = isSubmitWindowOpen(dto.windows.submit_until)}
    {@const canSubmitNow = dto.cycle_state === 'RECEPCION_IDEAS' && submitOpen}
    {@const canVoteNow = dto.cycle_state === 'VOTACION'}
    {#if ctaLabel}
      {#if canSubmitNow}
        <button
          class="cta"
          type="button"
          onclick={() => (modalOpen = true)}
          data-testid="cta"
        >
          {ctaLabel}
        </button>
      {:else if canVoteNow}
        <button
          class="cta"
          type="button"
          onclick={() => router.navigate('/vote')}
          data-testid="cta"
        >
          {ctaLabel}
        </button>
      {:else}
        <button class="cta" type="button" disabled aria-disabled="true" data-testid="cta">
          {ctaLabel}
          <small>(próximamente)</small>
        </button>
      {/if}
    {/if}

    <div class="my-twists">
      <MyTwistsPanel canDelete={canSubmitNow} />
    </div>

    <TwistModal
      open={modalOpen}
      chapterId={dto.chapter.id}
      onClose={() => (modalOpen = false)}
    />
  {:else if chapterStore.status === 'maintenance'}
    <section class="banner maintenance" data-testid="maintenance">
      <h2>En mantenimiento</h2>
      {#if chapterStore.maintenanceReason}
        <p>{chapterStore.maintenanceReason}</p>
      {:else}
        <p>Volvemos en un rato.</p>
      {/if}
    </section>
  {:else if chapterStore.status === 'no_season'}
    <section class="banner empty" data-testid="no-season">
      <h2>La historia todavía no empezó</h2>
      <p>Cuando el PO publique la primera temporada vas a ver el día 1 acá.</p>
    </section>
  {:else if chapterStore.status === 'no_release'}
    <section class="banner countdown-only" data-testid="no-release">
      <h2>Capítulo 1 en camino</h2>
      <p>Estrena {formatFirstRelease(chapterStore.firstReleaseAt)}.</p>
    </section>
  {:else}
    <section class="banner error" data-testid="error">
      <h2>Algo salió mal</h2>
      <p>No pudimos cargar el capítulo. Probá de nuevo en un momento.</p>
      <button type="button" onclick={() => void chapterStore.load()}>Reintentar</button>
    </section>
  {/if}
</main>

<style>
  .today {
    max-width: 720px;
    margin: 1rem auto 3rem;
    padding: 1rem;
    font-family: system-ui, sans-serif;
    color: #1a1a2e;
  }

  .meta {
    text-align: center;
    margin-bottom: 1.5rem;
  }

  .badge {
    display: inline-block;
    padding: 0.25rem 0.75rem;
    border-radius: 999px;
    background: #1a1a2e;
    color: #fff;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  h1 {
    font-size: 1.75rem;
    margin: 0.5rem 0 0.25rem;
  }

  .day {
    color: #666;
    font-size: 0.9rem;
    margin: 0;
  }

  .countdown-wrap {
    display: flex;
    justify-content: center;
    margin: 1.5rem 0;
  }

  .cta {
    display: block;
    width: 100%;
    padding: 1rem;
    background: #1a1a2e;
    color: #fff;
    border: 0;
    border-radius: 0.5rem;
    font-size: 1.05rem;
    cursor: not-allowed;
    opacity: 0.7;
  }

  .cta small {
    display: block;
    font-size: 0.8rem;
    opacity: 0.7;
    margin-top: 0.25rem;
  }

  .cta:not(:disabled) {
    cursor: pointer;
    opacity: 1;
  }

  .my-twists {
    margin-top: 1.5rem;
  }

  .banner {
    text-align: center;
    padding: 2rem 1rem;
    border-radius: 0.5rem;
    margin-top: 2rem;
  }

  .banner h2 {
    margin: 0 0 0.5rem;
  }

  .banner.maintenance {
    background: #fff4e5;
    color: #5a3d00;
  }

  .banner.empty {
    background: #f0f0f5;
    color: #555;
  }

  .banner.countdown-only {
    background: #e8f0fe;
    color: #1a3a8a;
  }

  .banner.error {
    background: #fce8e6;
    color: #6e1414;
  }

  .banner button {
    margin-top: 1rem;
    padding: 0.5rem 1.5rem;
    background: #1a1a2e;
    color: #fff;
    border: 0;
    border-radius: 0.25rem;
    cursor: pointer;
  }

  .loading-skeleton {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    margin-top: var(--space-5);
  }
</style>
