<script lang="ts">
  /**
   * /vote route — vote-feed screen for VOTACION.
   *
   * Module 007 / Task T-012.
   *
   * Renders a list of approved twists (FeedItem cards) with the user's
   * vote indicator + sort toggle + cursor pagination.
   *
   * Loads voteStore on mount. The store handles optimistic UI + rollback
   * + window/maintenance states. This component is just the shell.
   */
  import { onMount } from 'svelte';
  import MyVotesIndicator from '../lib/components/MyVotesIndicator.svelte';
  import VoteCard from '../lib/components/VoteCard.svelte';
  import { router } from '../lib/router.svelte';
  import type { SortMode } from '../lib/vote-api';
  import { voteStore } from '../lib/vote-store.svelte';

  onMount(() => {
    void voteStore.load({ sort: 'random' });
  });

  async function switchSort(sort: SortMode): Promise<void> {
    if (sort === voteStore.sort) return;
    await voteStore.load({ sort });
  }

  async function loadMore(): Promise<void> {
    if (!voteStore.page.next_cursor) return;
    await voteStore.load({
      sort: voteStore.sort,
      cursor: voteStore.page.next_cursor,
    });
  }

  async function handleVote(twistId: string): Promise<void> {
    await voteStore.cast(twistId);
  }

  function goHome(): void {
    router.navigate('/today');
  }

  const quotaExhausted = $derived(voteStore.quota.remaining <= 0);
</script>

<main class="vote-screen">
  <header class="meta">
    <button type="button" class="back" onclick={goHome} aria-label="Volver al día">
      ← Volver
    </button>
    <h1>Votá las mejores ideas</h1>
    <MyVotesIndicator />
  </header>

  <nav class="sort" aria-label="Orden">
    <button
      type="button"
      class:active={voteStore.sort === 'random'}
      onclick={() => void switchSort('random')}
    >
      Para vos
    </button>
    <button
      type="button"
      class:active={voteStore.sort === 'recent'}
      onclick={() => void switchSort('recent')}
    >
      Recientes
    </button>
    <button
      type="button"
      class:active={voteStore.sort === 'hot'}
      onclick={() => void switchSort('hot')}
    >
      Populares
    </button>
  </nav>

  {#if voteStore.status === 'loading' && voteStore.items.length === 0}
    <section class="skeleton" data-testid="loading">
      <div class="block"></div>
      <div class="block"></div>
      <div class="block"></div>
    </section>
  {:else if voteStore.status === 'maintenance'}
    <section class="banner maintenance" data-testid="maintenance">
      <h2>En mantenimiento</h2>
      <p>Volvemos en un rato.</p>
    </section>
  {:else if voteStore.status === 'error' && voteStore.errorMessage}
    <section class="banner error" data-testid="error">
      <h2>No pudimos cargar el feed</h2>
      <p>{voteStore.errorMessage}</p>
      <button type="button" onclick={() => void voteStore.load()}>Reintentar</button>
    </section>
  {:else if voteStore.items.length === 0}
    <section class="banner empty" data-testid="empty">
      <h2>No hay ideas aprobadas todavía</h2>
      <p>Volvé en unos minutos.</p>
    </section>
  {:else}
    {#if quotaExhausted}
      <p class="quota-banner" data-testid="quota-exhausted">
        Ya usaste todos tus votos. Esperá al próximo capítulo.
      </p>
    {/if}

    {#if voteStore.errorMessage}
      <p class="toast" role="alert" data-testid="toast">
        {voteStore.errorMessage}
      </p>
    {/if}

    <ul class="cards">
      {#each voteStore.items as item (item.id)}
        <li>
          <VoteCard
            {item}
            disabled={quotaExhausted}
            onvote={handleVote}
          />
        </li>
      {/each}
    </ul>

    {#if voteStore.page.next_cursor}
      <button
        type="button"
        class="more"
        onclick={() => void loadMore()}
        disabled={voteStore.status === 'loading'}
      >
        {voteStore.status === 'loading' ? 'Cargando...' : 'Cargar más'}
      </button>
    {/if}
  {/if}
</main>

<style>
  .vote-screen {
    max-width: 720px;
    margin: 1rem auto 3rem;
    padding: 1rem;
    font-family: system-ui, sans-serif;
    color: #1a1a2e;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
    margin-bottom: 1rem;
  }
  .back {
    background: none;
    border: 0;
    color: #1976d2;
    cursor: pointer;
    font: inherit;
    padding: 0.25rem 0.5rem;
  }
  h1 {
    flex: 1;
    margin: 0;
    font-size: 1.4rem;
  }
  .sort {
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1rem;
  }
  .sort button {
    flex: 1;
    padding: 0.5rem;
    border: 1px solid #ccc;
    background: white;
    color: #555;
    border-radius: 0.25rem;
    cursor: pointer;
    font-size: 0.9rem;
  }
  .sort button.active {
    background: #1a1a2e;
    color: white;
    border-color: #1a1a2e;
  }
  .cards {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .quota-banner {
    background: #fff3cd;
    color: #856404;
    padding: 0.75rem;
    border-radius: 0.25rem;
    margin: 0 0 1rem 0;
    text-align: center;
    font-size: 0.9rem;
  }
  .toast {
    background: #f8d7da;
    color: #721c24;
    padding: 0.75rem;
    border-radius: 0.25rem;
    margin: 0 0 1rem 0;
    text-align: center;
    font-size: 0.9rem;
  }
  .more {
    margin: 1rem auto 0;
    display: block;
    padding: 0.5rem 1.5rem;
    background: white;
    color: #1a1a2e;
    border: 1px solid #ccc;
    border-radius: 0.25rem;
    cursor: pointer;
  }
  .more:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .skeleton {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .skeleton .block {
    height: 5rem;
    background: linear-gradient(90deg, #f0f0f0 25%, #e8e8e8 50%, #f0f0f0 75%);
    background-size: 200% 100%;
    animation: shimmer 1.4s infinite;
    border-radius: 0.5rem;
  }
  .banner {
    text-align: center;
    padding: 2rem 1rem;
    border-radius: 0.5rem;
  }
  .banner.maintenance {
    background: #fff4e5;
    color: #5a3d00;
  }
  .banner.empty {
    background: #f0f0f5;
    color: #555;
  }
  .banner.error {
    background: #fce8e6;
    color: #6e1414;
  }
  .banner button {
    margin-top: 1rem;
    padding: 0.5rem 1.5rem;
    background: #1a1a2e;
    color: white;
    border: 0;
    border-radius: 0.25rem;
    cursor: pointer;
  }
  @keyframes shimmer {
    0% {
      background-position: 200% 0;
    }
    100% {
      background-position: -200% 0;
    }
  }
</style>
