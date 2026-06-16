<script lang="ts">
  /**
   * /series — series album view.
   *
   * Shows all released chapters of the active season in a Netflix/Spotify-style
   * list. No idea submission or voting CTAs here — those live in /today.
   */
  import { onMount } from 'svelte';
  import { apiFetch } from '../lib/api';
  import { router } from '../lib/router.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';

  // ---------------------------------------------------------------------------
  // Types
  // ---------------------------------------------------------------------------

  interface ChapterItem {
    id: string;
    day_index: number;
    title: string;
    synopsis: string;
    released_at: string;
    cover_image_url: string | null;
  }

  interface SeasonBrief {
    slug: string;
    title: string;
  }

  interface SeasonChaptersResponse {
    season: SeasonBrief;
    chapters: ChapterItem[];
  }

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  type Status = 'loading' | 'ok' | 'empty' | 'error';

  let status = $state<Status>('loading');
  let season = $state<SeasonBrief | null>(null);
  let chapters = $state<ChapterItem[]>([]);

  // ---------------------------------------------------------------------------
  // Fetch
  // ---------------------------------------------------------------------------

  async function load(): Promise<void> {
    status = 'loading';
    const result = await apiFetch<SeasonChaptersResponse>('/api/v1/seasons/current/chapters');
    if (result.ok && result.data) {
      season = result.data.season;
      chapters = [...result.data.chapters].reverse(); // latest first
      status = result.data.chapters.length > 0 ? 'ok' : 'empty';
    } else {
      status = 'error';
    }
  }

  onMount(() => {
    void load();
  });

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString('es-AR', {
      day: 'numeric',
      month: 'short',
    });
  }

  function openChapter(id: string): void {
    router.navigate(`/chapter/${id}`);
  }
</script>

<main class="series">
  {#if status === 'loading'}
    <header class="hero loading">
      <Skeleton height="1.5rem" width="60%" />
      <Skeleton height="1rem" width="30%" />
    </header>
    <div class="list">
      {#each [1, 2, 3] as i (i)}
        <div class="card skeleton-card">
          <Skeleton height="80px" width="80px" radius="var(--radius-sm)" />
          <div class="card-body">
            <Skeleton height="0.75rem" width="30%" />
            <Skeleton height="1rem" width="80%" />
            <Skeleton height="0.85rem" width="100%" />
          </div>
        </div>
      {/each}
    </div>

  {:else if status === 'ok' && season}
    <header class="hero">
      <p class="season-label">Temporada</p>
      <h1 class="season-title">{season.title}</h1>
      <p class="episode-count">{chapters.length} {chapters.length === 1 ? 'episodio' : 'episodios'}</p>
    </header>

    <div class="list">
      {#each chapters as ch (ch.id)}
        <button class="card" type="button" onclick={() => openChapter(ch.id)}>
          <div class="thumbnail">
            {#if ch.cover_image_url}
              <img src={ch.cover_image_url} alt={ch.title} loading="lazy" />
            {:else}
              <div class="thumbnail-placeholder">
                <span>Cap. {ch.day_index}</span>
              </div>
            {/if}
            <span class="day-badge">Cap. {ch.day_index}</span>
          </div>
          <div class="card-body">
            <p class="card-date">{formatDate(ch.released_at)}</p>
            <h2 class="card-title">{ch.title}</h2>
            <p class="card-synopsis">{ch.synopsis}</p>
          </div>
          <span class="card-arrow" aria-hidden="true">›</span>
        </button>
      {/each}
    </div>

  {:else if status === 'empty'}
    <header class="hero">
      <p class="season-label">Temporada</p>
      <h1 class="season-title">{season?.title ?? ''}</h1>
    </header>
    <div class="banner">
      <p>El primer capítulo todavía no fue publicado. Volvé pronto.</p>
    </div>

  {:else}
    <div class="banner error">
      <p>No pudimos cargar los episodios.</p>
      <button type="button" onclick={() => void load()}>Reintentar</button>
    </div>
  {/if}
</main>

<style>
  .series {
    max-width: 720px;
    margin: 0 auto;
    padding-bottom: 2rem;
    color: var(--color-text);
  }

  /* ── Hero ─────────────────────────────────────────────────────────────── */

  .hero {
    padding: var(--space-6) var(--space-4) var(--space-5);
    background: linear-gradient(180deg, rgba(255, 46, 99, 0.12) 0%, transparent 100%);
    border-bottom: 1px solid var(--color-border);
    margin-bottom: var(--space-2);
  }

  .hero.loading {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-6) var(--space-4);
  }

  .season-label {
    font-size: var(--font-size-xs);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--color-accent);
    margin: 0 0 var(--space-1);
    font-weight: 600;
  }

  .season-title {
    font-size: 1.8rem;
    font-weight: 800;
    margin: 0 0 var(--space-1);
    line-height: 1.15;
    background: linear-gradient(135deg, var(--color-text) 60%, var(--color-text-muted));
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .episode-count {
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
    margin: 0;
  }

  /* ── List ─────────────────────────────────────────────────────────────── */

  .list {
    display: flex;
    flex-direction: column;
    padding: 0 var(--space-2);
    gap: 2px;
  }

  /* ── Card ─────────────────────────────────────────────────────────────── */

  .card {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-3);
    background: transparent;
    border: none;
    border-radius: var(--radius-md);
    cursor: pointer;
    text-align: left;
    width: 100%;
    transition: background var(--motion-fast) var(--motion-easing);
    color: var(--color-text);
  }

  .card:hover,
  .card:focus-visible {
    background: rgba(255, 255, 255, 0.04);
    outline: none;
  }

  .card:active {
    background: rgba(255, 255, 255, 0.07);
  }

  /* ── Thumbnail ────────────────────────────────────────────────────────── */

  .thumbnail {
    position: relative;
    flex-shrink: 0;
    width: 88px;
    height: 88px;
    border-radius: var(--radius-sm);
    overflow: hidden;
    background: var(--color-surface);
  }

  .thumbnail img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  .thumbnail-placeholder {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(255, 46, 99, 0.1);
    color: var(--color-text-muted);
    font-size: var(--font-size-xs);
    font-weight: 600;
    letter-spacing: 0.05em;
  }

  .day-badge {
    position: absolute;
    bottom: 4px;
    left: 4px;
    background: rgba(0, 0, 0, 0.75);
    color: #fff;
    font-size: 0.65rem;
    font-weight: 700;
    padding: 1px 5px;
    border-radius: 4px;
    letter-spacing: 0.03em;
  }

  /* ── Card body ────────────────────────────────────────────────────────── */

  .card-body {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .card-date {
    font-size: var(--font-size-xs);
    color: var(--color-accent);
    margin: 0;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .card-title {
    font-size: var(--font-size-base);
    font-weight: 700;
    margin: 0;
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .card-synopsis {
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
    margin: 0;
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .card-arrow {
    flex-shrink: 0;
    font-size: 1.4rem;
    color: var(--color-text-muted);
    line-height: 1;
  }

  /* ── Skeleton card ────────────────────────────────────────────────────── */

  .skeleton-card {
    display: flex;
    gap: var(--space-3);
    padding: var(--space-3);
    align-items: flex-start;
  }

  .skeleton-card .card-body {
    gap: var(--space-2);
  }

  /* ── Banner ───────────────────────────────────────────────────────────── */

  .banner {
    text-align: center;
    padding: var(--space-8) var(--space-4);
    color: var(--color-text-muted);
  }

  .banner.error {
    color: var(--color-text-muted);
  }

  .banner button {
    margin-top: var(--space-3);
    padding: var(--space-2) var(--space-5);
    background: var(--color-accent);
    color: #fff;
    border: none;
    border-radius: var(--radius-sm);
    cursor: pointer;
    font-size: var(--font-size-sm);
    font-weight: 600;
  }
</style>
