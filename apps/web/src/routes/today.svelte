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
  import { windowFor } from '../lib/window-countdown';
  import Countdown from '../lib/Countdown.svelte';

  onMount(() => {
    void chapterStore.load();
  });

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
    <section class="skeleton" data-testid="loading">
      <div class="block"></div>
      <div class="block"></div>
      <div class="block"></div>
    </section>
  {:else if chapterStore.status === 'ok' && chapterStore.data}
    {@const dto = chapterStore.data}
    <header class="meta">
      <span class="badge" data-testid="state-badge">{stateBadge(dto.cycle_state)}</span>
      <h1>{dto.chapter.title}</h1>
      <p class="day">Día {dto.chapter.day_index} · {dto.season.title}</p>
      <p class="synopsis">{dto.chapter.synopsis}</p>
    </header>

    <section class="panels">
      {#each dto.chapter.panels as panel (panel.idx)}
        <figure class="panel">
          <img
            src={panel.image_url}
            alt={panel.narration}
            loading="lazy"
            data-testid="panel-image"
          />
          <figcaption>{panel.narration}</figcaption>
        </figure>
      {/each}
    </section>

    <p class="cliffhanger">{dto.chapter.cliffhanger}</p>

    {@const cd = windowFor(dto.cycle_state, dto.windows)}
    <div class="countdown-wrap">
      <Countdown label={cd.label} target={cd.target} />
    </div>

    {@const ctaLabel = ctaForState(dto.cycle_state)}
    {#if ctaLabel}
      <button class="cta" type="button" disabled aria-disabled="true" data-testid="cta">
        {ctaLabel}
        <small>(próximamente)</small>
      </button>
    {/if}
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
      <button type="button" on:click={() => void chapterStore.load()}>Reintentar</button>
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

  .synopsis {
    color: #444;
    margin: 1rem 0 0;
  }

  .panels {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    margin: 1.5rem 0;
  }

  .panel {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .panel img {
    width: 100%;
    height: auto;
    border-radius: 0.5rem;
    background: #f0f0f0;
  }

  .panel figcaption {
    color: #333;
    font-size: 0.95rem;
    line-height: 1.4;
  }

  .cliffhanger {
    font-style: italic;
    text-align: center;
    margin: 2rem auto;
    font-size: 1.1rem;
    color: #1a1a2e;
    max-width: 30em;
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

  .skeleton {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    margin-top: 2rem;
  }

  .skeleton .block {
    height: 6rem;
    background: linear-gradient(90deg, #f0f0f0 25%, #e8e8e8 50%, #f0f0f0 75%);
    background-size: 200% 100%;
    animation: skeleton-shimmer 1.4s infinite;
    border-radius: 0.5rem;
  }

  @keyframes skeleton-shimmer {
    0% {
      background-position: 200% 0;
    }
    100% {
      background-position: -200% 0;
    }
  }
</style>
